"""Release-time audit for an ``AutopilotRun``.

The bridge writes ``submit_decision`` when it emits, but an order held
``pending_open`` is decided at the OPEN, hours or days later — and until now
nothing wrote that outcome back. A batch whose orders were rejected at release
(risk gate, venue fit) kept reading "submitted, N held for open" in the history
forever.

``record_release_outcome`` closes that loop: it appends one ``release`` record
to the run's ``submit_decision``, refreshes the per-item statuses the bridge
recorded, and carries the shadow daily-cap evaluation
(:mod:`apps.portfolios.release_caps`). Best-effort — never raises into the
release path.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.utils import timezone

log = logging.getLogger(__name__)


def _jsonable(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


def record_release_outcome(
    run_id: int,
    released: list[int] | None = None,
    skipped: list[int] | None = None,
    failed: list[int] | None = None,
    caps_shadow: dict | None = None,
) -> dict | None:
    """Write one release outcome onto ``AutopilotRun(run_id).submit_decision``.

    ``released`` / ``skipped`` (deferred to a later tick) / ``failed`` (rejected
    or errored at the open) are BrokerOrder ids. ``caps_shadow`` is the
    non-blocking daily-cap evaluation for the batch. Returns the record, or
    ``None`` when the run is gone / the write failed.
    """
    from .models import AutopilotRun

    try:
        run = AutopilotRun.objects.filter(pk=run_id).first()
        if run is None:
            return None
        record = {
            "at": timezone.now().isoformat(),
            "released": list(released or []),
            "skipped": list(skipped or []),
            "failed": list(failed or []),
        }
        if caps_shadow is not None:
            record["caps_shadow"] = _jsonable(caps_shadow)
        decision = dict(run.submit_decision or {})
        decision["release"] = [*decision.get("release", []), record]
        if caps_shadow is not None:
            # The LAST evaluation is what the fund card shows.
            decision["caps_shadow"] = _jsonable(caps_shadow)
        # Keep the per-item statuses honest: an item held pending_open that was
        # rejected/filled at the open must not keep reading "pending_open".
        statuses = _order_statuses(record)
        if statuses:
            decision["items"] = [
                {**item, "status": statuses.get(item.get("order_id"), item.get("status"))}
                if isinstance(item, dict) else item
                for item in decision.get("items", [])
            ]
        decision["pending_open"] = len([
            i for i in decision.get("items", [])
            if isinstance(i, dict) and i.get("status") == "pending_open"
        ])
        run.submit_decision = decision
        run.save(update_fields=["submit_decision"])
        return record
    except Exception:  # noqa: BLE001 — the audit must never break the release
        log.exception("release outcome audit failed run=%s", run_id)
        return None


def _order_statuses(record: dict) -> dict[int, str]:
    """Current DB status for every order named in a release record."""
    ids = [*record["released"], *record["skipped"], *record["failed"]]
    if not ids:
        return {}
    try:
        from apps.brokers.models import BrokerOrder

        return dict(BrokerOrder.objects.filter(pk__in=ids).values_list("pk", "status"))
    except Exception:  # noqa: BLE001
        return {}


# Risk-shaping config: a change to any of these means the validated backtest no
# longer describes what the pod will trade (§9 goes stale the moment they move).
RISK_CONFIG_FIELDS = (
    "kind", "universe_id",
    "target_gross_pct", "target_net_pct",
    "max_position_pct", "max_sector_pct", "min_position_pct",
    "top_k_longs", "top_k_shorts",
    "max_turnover_pct", "min_trade_notional_usd",
    "per_etf_max_pct", "per_etf_min_pct", "max_etfs_held",
    "rp_vol_target_annual", "rp_max_gross", "rp_max_equity_pct",
    "rebalance_band_pct", "vol_window_days",
)


def snapshot_risk_config(strategy) -> dict:
    """The risk-shaping fields as they stand right now (for a before/after diff)."""
    return {f: getattr(strategy, f, None) for f in RISK_CONFIG_FIELDS}


def disable_on_config_change(strategy, before: dict) -> list[str]:
    """Auto-disable an ENABLED autopilot whose strategy's risk config just moved.

    The §9 gate was enable-time only: editing caps / universe / kind after
    arming re-locked the toggle (``validation.passed=False``) while the autopilot
    stayed enabled and kept trading the edited, never-backtested config. Editing
    a risk field now disarms it with reason ``config_changed`` — re-validate and
    re-enable to arm the new config. Returns the changed field names ([] = no
    risk field moved / nothing was armed).
    """
    changed = [f for f in RISK_CONFIG_FIELDS if getattr(strategy, f, None) != before.get(f)]
    if not changed:
        return []
    ap = getattr(strategy, "autopilot", None)
    if ap is None or not ap.is_enabled:
        return changed
    from .models import AutopilotRun

    ap.is_enabled = False
    ap.next_run_at = None
    ap.save(update_fields=["is_enabled", "next_run_at", "updated_at"])
    message = (
        "Autopilot disarmed: " + ", ".join(sorted(changed)) + " changed after validation. "
        "Re-run the validation backtest and enable again to arm the new config."
    )
    try:
        AutopilotRun.objects.create(
            autopilot=ap, fire_time_utc=timezone.now(),
            status=AutopilotRun.SKIPPED,
            submit_decision={
                "disabled": "config_changed",
                "fields": sorted(changed),
                "message": message,
            },
            finished_at=timezone.now(),
        )
    except Exception:  # noqa: BLE001 — the audit row must never block the edit
        log.exception("config_changed audit row failed autopilot=%s", ap.pk)
    try:
        from apps.notifications.autopilot import HALT, notify_autopilot

        notify_autopilot(ap, HALT, message)
    except Exception:  # noqa: BLE001
        pass
    log.warning("autopilot disabled: config_changed autopilot=%s fields=%s", ap.pk, changed)
    return changed


def last_caps_shadow(autopilot) -> dict | None:
    """The most recent run's shadow daily-cap evaluation (fund member payload)."""
    from .models import AutopilotRun

    run = (
        AutopilotRun.objects.filter(autopilot=autopilot)
        .exclude(submit_decision={})
        .order_by("-fire_time_utc")
        .first()
    )
    if run is None:
        return None
    return (run.submit_decision or {}).get("caps_shadow")
