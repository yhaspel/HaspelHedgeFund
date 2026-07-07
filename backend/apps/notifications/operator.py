"""P5-SH WS2.2 — operator alerts (self-host, no paging vendor).

A single self-hosted instance has no on-call rotation; the "operator" is whoever
runs it — modeled here as the active superuser(s). Operational alerts (a failed
run, an orphan sweep, a repeated provider outage, a scheduled-run ceiling breach)
go to those users' existing email/Telegram notification channels, reusing
``send_notification`` and its per-user daily cap. Every function is best-effort
and never raises into the caller (a run/task must not fail because an alert did).
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache

from .models import NotificationChannel, NotificationEvent
from .services import send_notification

log = logging.getLogger(__name__)

# Provider-outage throttle (anti-fatigue, plan Risk #4): count failures per
# provider within a short window, alert once the threshold is crossed, then stay
# quiet for a cooldown so a sustained outage yields one ping, not a storm.
PROVIDER_FAIL_THRESHOLD = 3
PROVIDER_FAIL_WINDOW_S = 900       # strikes counted within 15 minutes
PROVIDER_ALERT_COOLDOWN_S = 3600   # at most one alert per provider per hour


def _channels_for_user(user) -> list[NotificationChannel]:
    return list(NotificationChannel.objects.filter(user=user, is_active=True))


def _operator_channels() -> list[NotificationChannel]:
    """Active channels for every active superuser — the self-host operator(s)."""
    ops = get_user_model().objects.filter(is_superuser=True, is_active=True)
    return list(NotificationChannel.objects.filter(user__in=ops, is_active=True))


def _emit(channels, subject: str, body: str, *, ticker: str = "") -> int:
    """Send to each channel; return how many were actually delivered."""
    if not channels:
        # Surface the drop — an operator with no configured channel would
        # otherwise lose every alert silently.
        log.warning("operator alert dropped (no active operator channel): %s", subject)
        return 0
    delivered = 0
    for ch in channels:
        try:
            ev = send_notification(ch, subject, body, ticker=ticker, triggered_by=None)
            if ev.delivery_status == NotificationEvent.SENT:
                delivered += 1
        except Exception:  # noqa: BLE001 — an alert must never break its caller
            log.exception("operator alert failed channel=%s", getattr(ch, "pk", "?"))
    return delivered


def notify_run_failed(run) -> int:
    """Alert the run's owner that a run failed (covers the WS1.2 budget abort)."""
    tickers = list(run.tickers or [])
    subject = f"[Run #{run.id}] failed"
    body = (
        f"Run #{run.id} for {', '.join(tickers) or 'n/a'} ended FAILED.\n"
        f"Reason: {(run.error_message or 'unknown').strip()[:600]}"
    )
    return _emit(
        _channels_for_user(run.user), subject, body,
        ticker=tickers[0] if tickers else "",
    )


def notify_orphan_sweep(count: int, *, run_ids=None) -> int:
    """Alert the operator that sweep_orphan_runs marked >0 abandoned runs failed."""
    if count <= 0:
        return 0
    subject = f"[Ops] Orphan sweep marked {count} run(s) failed"
    body = (
        f"sweep_orphan_runs marked {count} abandoned run(s) FAILED (no active "
        f"Celery task was found for them). Run ids: {list(run_ids or [])[:50]}.\n"
        "This usually means a worker died mid-run or the broker dropped the task."
    )
    return _emit(_operator_channels(), subject, body)


def notify_provider_outage(provider: str, *, strikes: int = 0, detail: str = "") -> int:
    """Alert the operator that a data provider looks down (already throttled)."""
    subject = f"[Ops] Data provider outage: {provider}"
    body = (
        f"Provider {provider!r} failed repeatedly ({strikes} strikes) and looks "
        f"unreachable. {detail}\n"
        "Reads are degrading to last-persisted values; check the provider's "
        "status and your network/keys."
    ).strip()
    return _emit(_operator_channels(), subject, body)


def notify_ceiling_breach(scheduled_run, *, est_usd, ceiling, degraded: bool = False) -> int:
    """Alert the operator that a scheduled run was skipped for exceeding its cost
    ceiling (the run-owner path already exists; this routes it to the operator too)."""
    label = getattr(scheduled_run, "name", "") or f"#{scheduled_run.pk}"
    extra = " even after degrading to the cheapest preset" if degraded else ""
    subject = "[Ops] Scheduled run skipped: cost ceiling exceeded"
    body = (
        f"Scheduled run {label} was skipped: estimated ${est_usd} exceeds the "
        f"${ceiling} cost ceiling{extra}."
    )
    return _emit(_operator_channels(), subject, body)


def record_provider_failure(provider: str, *, detail: str = "") -> bool:
    """Count a provider failure; alert the operator once per cooldown after the
    strike threshold is crossed. No-op in OFFLINE_MODE, where a fenced provider
    (ProviderOffline) is expected, not an incident. Best-effort, never raises."""
    if getattr(settings, "OFFLINE_MODE", False):
        return False
    try:
        strikes_key = f"provider_fail:{provider}"
        cooldown_key = f"provider_alerted:{provider}"
        # Atomic count: seed the key once (sets the window TTL), then incr — so
        # concurrent workers don't lose increments in a read-modify-write race.
        cache.add(strikes_key, 0, PROVIDER_FAIL_WINDOW_S)
        try:
            strikes = cache.incr(strikes_key)
        except ValueError:  # key expired between add and incr — count as strike 1
            cache.set(strikes_key, 1, PROVIDER_FAIL_WINDOW_S)
            strikes = 1
        if strikes < PROVIDER_FAIL_THRESHOLD:
            return False
        # cache.add creates the cooldown key only if absent and returns True to
        # exactly one caller — so a saturated outage sends one alert, not a storm.
        if not cache.add(cooldown_key, 1, PROVIDER_ALERT_COOLDOWN_S):
            return False
        notify_provider_outage(provider, strikes=strikes, detail=detail)
        return True
    except Exception:  # noqa: BLE001
        log.exception("record_provider_failure failed provider=%s", provider)
        return False
