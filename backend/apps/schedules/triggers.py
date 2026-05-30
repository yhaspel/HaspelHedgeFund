"""Cron parsing + market-aware gating for scheduled runs (P3b).

Cron expressions are standard 5-field crontab strings (e.g. ``25 9 * * 1-5`` =
weekdays 09:25). They are interpreted in the schedule's ``timezone`` (DST-aware
via ``zoneinfo``) and ``next_run_at`` is stored as UTC (the project's
``TIME_ZONE``). v1 constrains market-aware schedules to the NYSE calendar
(``apps.brokers.market_calendar`` is NY/ET-only).
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter
from django.utils import timezone

UTC = ZoneInfo("UTC")


def is_valid_cron(expr: str) -> bool:
    return bool(expr) and croniter.is_valid(expr)


def compute_next(cron_expr: str, tz_name: str, after: datetime | None = None) -> datetime:
    """Next fire time strictly after ``after`` (default: now), returned as
    timezone-aware UTC. Interprets ``cron_expr`` in ``tz_name``."""
    base = after or timezone.now()
    tz = ZoneInfo(tz_name)
    local_base = base.astimezone(tz)
    itr = croniter(cron_expr, local_base)
    nxt_local = itr.get_next(datetime)  # aware, in tz
    return nxt_local.astimezone(UTC)


def market_gate_ok(scheduled_run, fire_time: datetime) -> bool:
    """Whether a market-aware schedule should actually fire at ``fire_time``.

    Non-market-aware schedules always fire. Market-aware schedules skip NYSE
    non-trading days (weekends + holidays) — this is what makes a "weekday
    09:25 ET" schedule skip Thanksgiving etc. We gate on the *trading day*, not
    the open session, so pre-open schedules (09:25) still fire.
    """
    if not scheduled_run.is_market_aware:
        return True
    from apps.brokers.market_calendar import NY, is_trading_day

    ny_dt = fire_time.astimezone(NY)
    return is_trading_day(ny_dt.date())
