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

from datetime import date as date_cls
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)


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


def is_trading_day(day: date_cls) -> bool:
    if day.weekday() >= 5:  # 5=Sat, 6=Sun
        return False
    return day not in _HOLIDAYS


def is_market_open(now: datetime | None = None) -> bool:
    now = (now or datetime.now(tz=NY)).astimezone(NY)
    if not is_trading_day(now.date()):
        return False
    return REGULAR_OPEN <= now.time() < REGULAR_CLOSE


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
        return datetime.combine(cur.date(), REGULAR_CLOSE, tzinfo=NY)
    nxt = next_open(cur)
    return datetime.combine(nxt.date(), REGULAR_CLOSE, tzinfo=NY)


def session_summary(now: datetime | None = None) -> dict:
    """Convenience dict for the API/UI: open?, next_open, next_close."""
    cur = (now or datetime.now(tz=NY)).astimezone(NY)
    return {
        "is_open": is_market_open(cur),
        "now_eastern": cur.isoformat(),
        "next_open": next_open(cur).isoformat(),
        "next_close": next_close(cur).isoformat(),
    }
