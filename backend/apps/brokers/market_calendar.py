"""Shared exchange-calendar utility (XNYS).

This is the **only** market calendar in the codebase; P3b's scheduling
reuses it. The implementation is intentionally minimal: we hardcode the
regular session bounds (09:30–16:00 America/New_York) and a small set of
US equity holidays so the test suite stays deterministic without pulling
in the `exchange_calendars` package's network/CSV machinery. The shape of
the public API matches what `exchange_calendars` provides so swapping the
backing implementation later is mechanical.

Extended-hours trading is disabled by default and not configurable in
this sub-phase.
"""
from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
# NYSE half-days close at 13:00 ET (no extended session for us).
EARLY_CLOSE = time(13, 0)
# Warn once the static tables are about to run out. Six months is roughly two
# release cycles of head-room to refresh them.
TABLE_HORIZON_WARN = timedelta(days=183)


# US equity holidays through 2027. We deliberately include a few years out
# so demo/dev still produces sensible "next open" answers in 2026 (current
# project date) and 2027. Update annually.
_HOLIDAYS: set[date_cls] = {
    # 2025
    date_cls(2025, 1, 1),   # New Year
    date_cls(2025, 1, 20),  # MLK
    date_cls(2025, 2, 17),  # Presidents
    date_cls(2025, 4, 18),  # Good Friday
    date_cls(2025, 5, 26),  # Memorial
    date_cls(2025, 6, 19),  # Juneteenth
    date_cls(2025, 7, 4),   # Independence
    date_cls(2025, 9, 1),   # Labor
    date_cls(2025, 11, 27), # Thanksgiving
    date_cls(2025, 12, 25), # Christmas
    # 2026
    date_cls(2026, 1, 1),
    date_cls(2026, 1, 19),
    date_cls(2026, 2, 16),
    date_cls(2026, 4, 3),
    date_cls(2026, 5, 25),
    date_cls(2026, 6, 19),
    date_cls(2026, 7, 3),
    date_cls(2026, 9, 7),
    date_cls(2026, 11, 26),
    date_cls(2026, 12, 25),
    # 2027
    date_cls(2027, 1, 1),
    date_cls(2027, 1, 18),
    date_cls(2027, 2, 15),
    date_cls(2027, 3, 26),
    date_cls(2027, 5, 31),
    date_cls(2027, 6, 18),
    date_cls(2027, 7, 5),
    date_cls(2027, 9, 6),
    date_cls(2027, 11, 25),
    date_cls(2027, 12, 24),
}


# --- 13:00 ET early closes ---------------------------------------------------
#
# NYSE closes at 13:00 ET on three recurring half-days: the day after
# Thanksgiving, Christmas Eve and the day before Independence Day — the latter
# two only when they land on a weekday that is not itself a holiday (e.g.
# 2026-07-03 is the *observed* Independence Day, a full close, not a half-day).
# Derived from the rules rather than copied so the table can't drift.
_EARLY_CLOSE_YEARS = range(2025, 2029)


def _thanksgiving(year: int) -> date_cls:
    """Fourth Thursday of November."""
    d = date_cls(year, 11, 1)
    while d.weekday() != 3:  # 3 = Thursday
        d += timedelta(days=1)
    return d + timedelta(weeks=3)


def _build_early_closes() -> set[date_cls]:
    out: set[date_cls] = set()
    for year in _EARLY_CLOSE_YEARS:
        candidates = (
            _thanksgiving(year) + timedelta(days=1),  # "Black Friday"
            date_cls(year, 12, 24),                   # Christmas Eve
            date_cls(year, 7, 3),                     # day before Independence Day
        )
        for day in candidates:
            # A weekend or a full holiday is never a half-day.
            if day.weekday() >= 5 or day in _HOLIDAYS:
                continue
            out.add(day)
    return out


_EARLY_CLOSES: set[date_cls] = _build_early_closes()


def is_early_close(day: date_cls) -> bool:
    """True when `day` is a 13:00 ET half-session."""
    return day in _EARLY_CLOSES


def session_close(day: date_cls) -> time:
    """The closing wall-clock time (America/New_York) for `day`."""
    return EARLY_CLOSE if day in _EARLY_CLOSES else REGULAR_CLOSE


def table_horizon_warning(today: date_cls | None = None) -> str | None:
    """Message to log when the hardcoded tables are within
    ``TABLE_HORIZON_WARN`` of running out, else ``None``.

    Past the last tabulated year every holiday silently becomes a trading day
    and every half-day a full session, so orders would be released into a
    closed venue. Callers update the tables; this is the tripwire."""
    today = today or datetime.now(tz=NY).date()
    # The FIRST table to run out is the one that matters — the holiday table
    # ends in 2027 and the half-day table in 2028, so 2027-12-31 is the date
    # after which this calendar starts lying.
    last_covered = date_cls(min(max(_HOLIDAYS).year, max(_EARLY_CLOSES).year), 12, 31)
    if last_covered - today > TABLE_HORIZON_WARN:
        return None
    return (
        f"market_calendar: the static NYSE holiday / early-close tables end on "
        f"{last_covered.isoformat()} — refresh them before then or the calendar "
        f"will report holidays as ordinary trading days"
    )


def _warn_if_tables_expiring() -> None:
    message = table_horizon_warning()
    if message:
        log.warning("%s", message)


def is_trading_day(day: date_cls) -> bool:
    if day.weekday() >= 5:  # 5=Sat, 6=Sun
        return False
    return day not in _HOLIDAYS


def is_market_open(now: datetime | None = None) -> bool:
    now = (now or datetime.now(tz=NY)).astimezone(NY)
    if not is_trading_day(now.date()):
        return False
    return REGULAR_OPEN <= now.time() < session_close(now.date())


def next_open(after: datetime | None = None) -> datetime:
    """Return the start of the next regular session strictly after `after`."""
    cur = (after or datetime.now(tz=NY)).astimezone(NY)
    # If we're already before today's open and today is a trading day, that's the next open.
    today_open = datetime.combine(cur.date(), REGULAR_OPEN, tzinfo=NY)
    if cur < today_open and is_trading_day(cur.date()):
        return today_open
    day = cur.date() + timedelta(days=1)
    while not is_trading_day(day):
        day += timedelta(days=1)
    return datetime.combine(day, REGULAR_OPEN, tzinfo=NY)


def next_close(after: datetime | None = None) -> datetime:
    cur = (after or datetime.now(tz=NY)).astimezone(NY)
    if is_market_open(cur):
        return datetime.combine(cur.date(), session_close(cur.date()), tzinfo=NY)
    nxt = next_open(cur)
    return datetime.combine(nxt.date(), session_close(nxt.date()), tzinfo=NY)


def session_summary(now: datetime | None = None) -> dict:
    """Convenience dict for the API/UI: open?, next_open, next_close."""
    cur = (now or datetime.now(tz=NY)).astimezone(NY)
    return {
        "is_open": is_market_open(cur),
        "now_eastern": cur.isoformat(),
        "next_open": next_open(cur).isoformat(),
        "next_close": next_close(cur).isoformat(),
        "is_early_close": is_early_close(cur.date()),
    }


_warn_if_tables_expiring()
