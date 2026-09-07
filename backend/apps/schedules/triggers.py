"""Cron parsing + market-aware gating for scheduled runs (P3b).

Cron expressions are standard 5-field crontab strings (e.g. ``25 9 * * 1-5`` =
weekdays 09:25). They are interpreted in the schedule's ``timezone`` (DST-aware
via ``zoneinfo``) and ``next_run_at`` is stored as UTC (the project's
``TIME_ZONE``). v1 constrains market-aware schedules to the NYSE calendar
(``apps.brokers.market_calendar`` is NY/ET-only).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from croniter import CroniterError, croniter
from django.utils import timezone

UTC = ZoneInfo("UTC")

CRON_FIELDS = 5
# Minimum spacing between two consecutive fires. Each fire is a full council run
# PER WATCHLIST TICKER, so a 1-minute cadence on a 3-name watchlist is 4320 runs
# a day; 15 minutes is the tightest cadence the executor (which runs its child
# runs synchronously) can keep up with.
MIN_INTERVAL = timedelta(minutes=15)


def is_valid_cron(expr: str) -> bool:
    """Standard 5-field crontab only.

    croniter also accepts a 6-field form where the EXTRA field is seconds
    (last), while cron_descriptor reads 6 fields as Quartz (seconds FIRST) — so
    a 6-field expression makes the stored description contradict next_run_at.
    Neither library rejects it, so we do.
    """
    if not expr or len(expr.split()) != CRON_FIELDS:
        return False
    return croniter.is_valid(expr)


def describe_cron(expr: str, tz_name: str = "") -> str:
    """Human-readable description of a cron expression, e.g.
    ``"At 09:25 AM, Monday through Friday"``. Falls back to the raw expression
    if it can't be parsed.

    ``tz_name`` appends the zone the expression is interpreted in. Without it
    the description reads as a wall-clock time in the *viewer's* zone while
    ``next_run_at`` is rendered in the browser's zone — the two strings then
    disagree ("At 04:30 PM" next to "Fri 23:30") with nothing to explain why.
    """
    try:
        from cron_descriptor import Options, get_description

        # Pin the clock format. Left to cron_descriptor it follows the HOST
        # locale, so the same schedule reads "At 04:30 PM" on one machine and
        # "At 16:30" on another — the string is user-facing, so it must not
        # depend on the container's LANG.
        options = Options()
        options.use_24hour_time_format = False
        desc = get_description(expr, options)
    except Exception:  # noqa: BLE001 — never let description fail an API read
        desc = expr
    return f"{desc} ({tz_name})" if tz_name else desc


def next_fires(cron_expr: str, tz_name: str, count: int = 5, after: datetime | None = None):
    """The next ``count`` fire times as UTC datetimes.

    Raises ``ValueError`` when the expression can never fire (croniter searches
    forward a bounded number of years and then raises ``CroniterBadDateError`` —
    e.g. ``0 0 31 2 *``, February 31st). ``compute_next`` lets that escape as a
    500 from the create/update endpoint, so callers validating user input should
    go through here.
    """
    base = after or timezone.now()
    tz = ZoneInfo(tz_name)
    itr = croniter(cron_expr, base.astimezone(tz))
    out: list[datetime] = []
    try:
        for _ in range(count):
            out.append(itr.get_next(datetime).astimezone(UTC))
    except CroniterError as exc:
        raise ValueError(f"cron expression {cron_expr!r} never fires: {exc}") from exc
    return out


def compute_next(cron_expr: str, tz_name: str, after: datetime | None = None) -> datetime:
    """Next fire time strictly after ``after`` (default: now), returned as
    timezone-aware UTC. Interprets ``cron_expr`` in ``tz_name``."""
    base = after or timezone.now()
    tz = ZoneInfo(tz_name)
    local_base = base.astimezone(tz)
    itr = croniter(cron_expr, local_base)
    nxt_local = itr.get_next(datetime)  # aware, in tz
    return nxt_local.astimezone(UTC)


# Bound on how many missed slots we walk one at a time before jumping straight
# to "next fire after now" — a 1-minute cron and a week of downtime is 10k slots.
_MAX_CATCHUP_SCAN = 2000


def advance_after_downtime(
    cron_expr: str, tz_name: str, fire_time: datetime, now: datetime
) -> tuple[datetime, int]:
    """``(next fire strictly after ``now``, number of slots skipped)``.

    Beat downtime must cost at most ONE run, not one per missed slot: advancing
    ``next_run_at`` from the *fire time* makes the dispatcher re-select the same
    schedule on every subsequent tick until it has replayed every slot it
    missed (5 hours of downtime on an hourly schedule = 5 full council runs per
    ticker, all with stale as-of data).
    """
    nxt = compute_next(cron_expr, tz_name, after=fire_time)
    skipped = 0
    while nxt <= now and skipped < _MAX_CATCHUP_SCAN:
        nxt = compute_next(cron_expr, tz_name, after=nxt)
        skipped += 1
    if nxt <= now:  # pathological gap — jump the rest in one step
        nxt = compute_next(cron_expr, tz_name, after=now)
    return nxt, skipped


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
