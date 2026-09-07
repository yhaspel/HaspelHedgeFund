"""Scheduled-run dispatcher + executor (P3b).

``dispatch_due_scheduled_runs`` is a beat task (every 60s): it finds schedules
whose ``next_run_at`` has passed, advances them, and — for market-aware
schedules on a trading day — creates one idempotent history row per fire time
and hands it to ``execute_scheduled_run``.

``execute_scheduled_run`` does the cost-ceiling check (skip/degrade/notify_only),
fans out one single-ticker Run per watchlist ticker (respecting the load-bearing
single-ticker constraint), runs materiality gating per ticker, and notifies.

The child council runs are executed **synchronously** (``execute_run(run.id)``,
not ``.delay``) so materiality + notification can run immediately after all
tickers complete — scheduled runs are best-effort, so a sequential batch in one
worker slot is acceptable and far simpler than a chord+callback.
"""
from __future__ import annotations

import logging
from decimal import ROUND_DOWN, Decimal

from celery import shared_task
from django.utils import timezone

from apps.runs.models import Run
from apps.runs.tasks import execute_run

from .costs import cheaper_preset, estimate_run_cost, resolve_overrides
from .models import ScheduledRun, ScheduledRunHistory
from .triggers import advance_after_downtime, market_gate_ok

log = logging.getLogger(__name__)


def _alert_ceiling_breach(sr, est_usd, ceiling, *, degraded: bool) -> None:
    """P5-SH WS2.2: route a cost-ceiling skip to the operator too. Best-effort."""
    try:
        from apps.notifications.operator import notify_ceiling_breach

        notify_ceiling_breach(sr, est_usd=est_usd, ceiling=ceiling, degraded=degraded)
    except Exception:  # pragma: no cover — an alert must never break the dispatcher
        log.exception("ceiling-breach operator alert failed sr=%s", getattr(sr, "pk", "?"))


@shared_task(name="apps.schedules.tasks.dispatch_due_scheduled_runs")
def dispatch_due_scheduled_runs() -> dict:
    now = timezone.now()
    due_ids = list(
        ScheduledRun.objects.filter(
            is_active=True, next_run_at__isnull=False, next_run_at__lte=now
        ).values_list("id", flat=True)
    )
    dispatched = 0
    skipped_market = 0
    for sr_id in due_ids:
        sr = ScheduledRun.objects.filter(pk=sr_id).first()
        if sr is None:
            continue
        fire_time = sr.next_run_at
        # Advance the schedule FIRST so a slow/duplicate dispatcher pass can't
        # re-select this same fire time — and advance it to the next FUTURE
        # slot, so downtime costs one fire, not one per missed slot.
        try:
            next_at, catchup_skipped = advance_after_downtime(
                sr.cron_expression, sr.timezone, fire_time, now
            )
        except Exception:  # noqa: BLE001 — a bad stored cron must not stop the beat
            log.exception(
                "scheduled run %s has an unusable cron %r — deactivating its next fire",
                sr_id, sr.cron_expression,
            )
            ScheduledRun.objects.filter(pk=sr_id).update(next_run_at=None)
            continue
        sr.last_run_at = now
        sr.next_run_at = next_at
        sr.save(update_fields=["last_run_at", "next_run_at"])

        gate_ok = market_gate_ok(sr, fire_time)
        note = (
            f"catch-up: {catchup_skipped} slots skipped" if catchup_skipped else ""
        )
        if not gate_ok:
            # A market-holiday skip is a decision the user must be able to SEE:
            # without a row, the History view is simply blank for a fire the
            # "Next run" column had promised.
            skipped_market += 1
            _record_market_skip(sr, fire_time, note)
            continue

        # Idempotency: one history per (schedule, fire_time). A duplicate
        # dispatch gets the existing row (created=False) and bails.
        hist, created = ScheduledRunHistory.objects.get_or_create(
            scheduled_run=sr,
            fire_time_utc=fire_time,
            defaults={"status": ScheduledRunHistory.PENDING, "error": note},
        )
        if not created:
            continue
        execute_scheduled_run.delay(sr.id, hist.id)
        dispatched += 1

    return {"due": len(due_ids), "dispatched": dispatched, "skipped_market": skipped_market}


def _market_skip_reason(sr, fire_time) -> str:
    """Why the market gate refused this fire — 'weekend' or 'holiday'."""
    try:
        from apps.brokers.market_calendar import NY, is_trading_day

        day = fire_time.astimezone(NY).date()
        if day.weekday() >= 5:
            return "weekend"
        if not is_trading_day(day):
            return "holiday"
    except Exception:  # noqa: BLE001 — the label is cosmetic
        pass
    return "non-trading day"


def _record_market_skip(sr, fire_time, note: str = "") -> None:
    """Write the audit row for a fire the market gate skipped. Best-effort."""
    reason = _market_skip_reason(sr, fire_time)
    error = f"market closed ({reason})"
    if note:
        error = f"{error}; {note}"
    try:
        ScheduledRunHistory.objects.get_or_create(
            scheduled_run=sr,
            fire_time_utc=fire_time,
            defaults={
                "status": ScheduledRunHistory.SKIPPED,
                "error": error,
                "finished_at": timezone.now(),
            },
        )
    except Exception:  # noqa: BLE001 — an audit row must not break the beat
        log.exception("failed to record market-closed skip for schedule %s", sr.pk)


# Floor for a per-run hard budget: below this a run cannot complete even one
# agent, so a tight ceiling on a wide watchlist degrades to "abort immediately"
# rather than "spend a sane minimum and stop".
MIN_CHILD_RUN_BUDGET = Decimal("0.05")


def _per_run_budget(ceiling, n_tickers: int):
    """Each child run's share of the schedule's ceiling, or None when unset."""
    if ceiling is None or n_tickers <= 0:
        return None
    share = (Decimal(str(ceiling)) / Decimal(n_tickers)).quantize(
        Decimal("0.01"), rounding=ROUND_DOWN
    )
    return max(MIN_CHILD_RUN_BUDGET, share)


def _find_prior_run(user, ticker: str, before_run: Run):
    """Most recent prior DONE run for this user + ticker (single-ticker exact
    match — cross-DB safe, unlike a JSON ``contains`` lookup)."""
    return (
        Run.objects.filter(
            user=user,
            status=Run.DONE,
            tickers=[ticker],
            created_at__lt=before_run.created_at,
        )
        .exclude(pk=before_run.pk)
        .order_by("-created_at")
        .first()
    )


@shared_task(name="apps.schedules.tasks.execute_scheduled_run")
def execute_scheduled_run(scheduled_run_id: int, history_id: int) -> dict:
    sr = ScheduledRun.objects.select_related(
        "watchlist", "notification_channel"
    ).filter(pk=scheduled_run_id).first()
    hist = ScheduledRunHistory.objects.filter(pk=history_id).first()
    if sr is None or hist is None:
        return {"status": "missing"}

    hist.status = ScheduledRunHistory.RUNNING
    hist.save(update_fields=["status"])

    tickers = list(sr.watchlist.tickers.values_list("ticker", flat=True))
    if not tickers:
        hist.status = ScheduledRunHistory.DONE
        hist.finished_at = timezone.now()
        hist.error = "watchlist is empty"
        hist.save(update_fields=["status", "finished_at", "error"])
        return {"status": "empty"}

    # ---- cost ceiling ----
    # P4-OFF WS-1.5: at L1 the run is all-local ($0), so SKIP cost estimation
    # entirely — a stored expensive preset can't cost-block a scheduled run, and
    # estimating "local" would otherwise need a live Ollama probe that can crash
    # this task. Overrides are left empty (below); execute_run re-forces the local
    # preset at the execution seam, using the run user's own Ollama host.
    from django.conf import settings as dj_settings

    offline = getattr(dj_settings, "OFFLINE_MODE", False)
    preset = "local" if offline else sr.model_preset
    est = (
        {"est_total_usd": 0.0}
        if offline
        else estimate_run_cost(sr.user, preset, sr.model_overrides, sr.personas, len(tickers))
    )
    ceiling = float(sr.cost_ceiling_usd) if sr.cost_ceiling_usd is not None else None
    degraded_from = ""
    overage = False

    if ceiling is not None and est["est_total_usd"] > ceiling:
        if sr.on_breach == ScheduledRun.SKIP:
            hist.status = ScheduledRunHistory.SKIPPED
            hist.estimated_cost_usd = Decimal(str(est["est_total_usd"]))
            hist.error = (
                f"cost ceiling exceeded: est ${est['est_total_usd']} > ${ceiling}"
            )
            hist.finished_at = timezone.now()
            hist.save(update_fields=["status", "estimated_cost_usd", "error", "finished_at"])
            _alert_ceiling_breach(sr, est["est_total_usd"], ceiling, degraded=False)
            return {"status": "skipped_cost"}
        if sr.on_breach == ScheduledRun.DEGRADE:
            cur = preset
            while est["est_total_usd"] > ceiling:
                nxt = cheaper_preset(cur)
                if nxt is None or nxt == cur:
                    break
                cur = nxt
                est = estimate_run_cost(
                    sr.user, cur, sr.model_overrides, sr.personas, len(tickers)
                )
            if cur != preset:
                degraded_from = preset
                preset = cur
            if est["est_total_usd"] > ceiling:
                hist.status = ScheduledRunHistory.SKIPPED
                hist.estimated_cost_usd = Decimal(str(est["est_total_usd"]))
                hist.degraded_preset = preset if degraded_from else ""
                hist.error = "cost ceiling exceeded even after degrading to the cheapest preset"
                hist.finished_at = timezone.now()
                hist.save(update_fields=[
                    "status", "estimated_cost_usd", "degraded_preset", "error", "finished_at",
                ])
                _alert_ceiling_breach(sr, est["est_total_usd"], ceiling, degraded=True)
                return {"status": "skipped_after_degrade"}
        elif sr.on_breach == ScheduledRun.NOTIFY_ONLY:
            overage = True

    overrides = {} if offline else resolve_overrides(sr.user, preset, sr.model_overrides)
    hist.estimated_cost_usd = Decimal(str(est["est_total_usd"]))
    hist.degraded_preset = degraded_from
    hist.save(update_fields=["estimated_cost_usd", "degraded_preset"])

    # ---- fan out: one single-ticker Run per watchlist ticker ----
    as_of = timezone.localdate()
    personas = list(sr.personas or [])
    # P4c: when the schedule pins an agent-graph version, each child Run inherits
    # it (resolve_graph compiles it). Flatten the version's per-node models over
    # the preset (graph wins for the agents it specifies) and adopt its persona
    # subset, so the chosen models actually take effect at runtime.
    graph_version = sr.graph_version
    if graph_version is not None:
        from apps.graphs.submission import model_overrides_for_version, personas_for_version

        overrides = {**overrides, **model_overrides_for_version(graph_version)}
        gpersonas = personas_for_version(graph_version)
        if gpersonas:
            personas = gpersonas
    # The cost ceiling used to gate the pre-flight ESTIMATE only: once the fan-out
    # started, actual spend was unbounded (an estimate that lands under the
    # ceiling says nothing about what 16 agents × N tickers really cost). Give
    # every child run a hard mid-run budget — its share of the ceiling — so
    # record_llm_call aborts a runaway instead of the ceiling being advisory.
    per_run_budget = _per_run_budget(sr.cost_ceiling_usd, len(tickers))
    run_ids: list[int] = []
    for tk in tickers:
        run = Run.objects.create(
            user=sr.user,
            tickers=[tk],
            model_overrides=overrides,
            as_of_date=as_of,
            personas=personas,
            source=Run.ADHOC,
            graph_version=graph_version,
            max_budget_usd=per_run_budget,
        )
        run_ids.append(run.id)
        try:
            execute_run(run.id)  # synchronous — see module docstring
        except Exception:  # noqa: BLE001 — one ticker failing must not abort the batch
            log.exception("scheduled run: ticker %s failed (run=%s)", tk, run.id)
    hist.runs.set(run_ids)

    # ---- materiality + notify ----
    from django.conf import settings as dj_settings

    from apps.notifications import materiality
    from apps.notifications.content import build_content, build_digest_content
    from apps.notifications.services import send_notification

    channel = sr.notification_channel
    summary = []
    actual_cost = Decimal("0")
    material: list[tuple] = []  # (run, result) for runs that should notify
    for run_id in run_ids:
        run = Run.objects.filter(pk=run_id).first()
        if run is None:
            continue
        actual_cost += run.total_cost_usd or Decimal("0")
        if run.status != Run.DONE:
            summary.append({"ticker": run.tickers[0], "notify": False, "status": run.status})
            continue
        ticker = run.tickers[0]
        prior = _find_prior_run(sr.user, ticker, run)
        result = materiality.evaluate(run, prior)
        result["ticker"] = ticker
        result["run_id"] = run_id
        if overage:
            result.setdefault("reasons", []).insert(0, "cost ceiling overage (notify_only)")
            result["notify"] = True
        summary.append(result)
        if result["notify"]:
            material.append((run, result))

    # One digest when a single fire surfaces many material events (plan refinement
    # #4), else an individual message per ticker (per-ticker throttle applies).
    notified = 0
    digest = False
    if channel is not None and channel.is_active and material:
        threshold = int(getattr(dj_settings, "NOTIFICATIONS_DIGEST_THRESHOLD", 5))
        if len(material) > threshold:
            content = build_digest_content([r for _, r in material], sr)
            ev = send_notification(
                channel, content["subject"], content["text"],
                html_body=content["html"], triggered_by=hist,
            )
            digest = ev.delivery_status == ev.SENT
            notified = len(material) if digest else 0
        else:
            for run, result in material:
                content = build_content(run, result)
                ev = send_notification(
                    channel, content["subject"], content["text"],
                    html_body=content["html"], triggered_by=hist,
                    ticker=result["ticker"],
                )
                if ev.delivery_status == ev.SENT:
                    notified += 1

    # ---- paper auto-submit (opt-in, paper-only) ----
    from apps.schedules.autosubmit import auto_submit_orders
    submit_decision = auto_submit_orders(sr, hist, run_ids)

    hist.materiality_decision = {
        "runs": summary,
        "degraded_from": degraded_from,
        "active_preset": preset,
        "overage": overage,
        "digest": digest,
    }
    hist.submit_decision = submit_decision
    hist.notified_count = notified
    hist.actual_cost_usd = actual_cost
    hist.status = ScheduledRunHistory.DONE
    hist.finished_at = timezone.now()
    hist.save(
        update_fields=[
            "materiality_decision", "submit_decision", "notified_count",
            "actual_cost_usd", "status", "finished_at",
        ]
    )
    return {
        "status": "done", "tickers": len(run_ids), "notified": notified,
        "submitted": submit_decision.get("submitted", 0),
    }
