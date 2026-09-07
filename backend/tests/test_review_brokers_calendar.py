"""Adversarial review (reviewer: brokers) — market_calendar proof tests.

Checks the hard-coded XNYS holiday table against the NYSE holiday RULES
(computed, not copied), the DST handling of the 09:30 ET open, the 2026-09-07
Labor Day / 2026-04-03 Good Friday / 2027 observed-holiday cases, and the
(unmodelled) 13:00 ET early closes.

Tests marked ``xfail(strict=True)`` document a real defect in the current code:
they PASS only while the defect is present (pytest reports them as XFAIL), and
will flip to XPASS/failure the moment the defect is fixed.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from apps.brokers import market_calendar as cal

NY = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# NYSE holiday rules, computed independently of the table under test
# ---------------------------------------------------------------------------


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm (Meeus/Jones/Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def _observed(d: date, *, saturday_to_friday: bool = True) -> date | None:
    """NYSE observance: Sat → preceding Friday (except New Year's Day, which
    is simply not observed when it falls on a Saturday); Sun → following Monday."""
    if d.weekday() == 5:
        return d - timedelta(days=1) if saturday_to_friday else None
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def nyse_holidays(year: int) -> set[date]:
    out: set[date] = set()
    ny = _observed(date(year, 1, 1), saturday_to_friday=False)
    if ny is not None:
        out.add(ny)
    out.add(_nth_weekday(year, 1, 0, 3))          # MLK — 3rd Monday Jan
    out.add(_nth_weekday(year, 2, 0, 3))          # Presidents — 3rd Monday Feb
    out.add(_easter(year) - timedelta(days=2))    # Good Friday
    out.add(_last_weekday(year, 5, 0))            # Memorial — last Monday May
    out.add(_observed(date(year, 6, 19)))         # Juneteenth
    out.add(_observed(date(year, 7, 4)))          # Independence Day
    out.add(_nth_weekday(year, 9, 0, 1))          # Labor — 1st Monday Sep
    out.add(_nth_weekday(year, 11, 3, 4))         # Thanksgiving — 4th Thursday Nov
    out.add(_observed(date(year, 12, 25)))        # Christmas
    return out


# ---------------------------------------------------------------------------
# Holiday table correctness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("year", [2025, 2026, 2027])
def test_holiday_table_matches_nyse_rules(year):
    expected = nyse_holidays(year)
    actual = {d for d in cal._HOLIDAYS if d.year == year}
    assert actual == expected, (
        f"{year}: missing={sorted(expected - actual)} extra={sorted(actual - expected)}"
    )


def test_specific_dates_named_in_the_brief():
    # 2026
    assert not cal.is_trading_day(date(2026, 9, 7)), "Labor Day 2026"
    assert not cal.is_trading_day(date(2026, 4, 3)), "Good Friday 2026 (Easter 2026-04-05)"
    assert not cal.is_trading_day(date(2026, 6, 19)), "Juneteenth 2026 (Friday)"
    assert not cal.is_trading_day(date(2026, 7, 3)), "Independence Day observed (Jul 4 = Sat)"
    assert cal.is_trading_day(date(2026, 9, 8))
    # 2027 — every observed shift
    assert not cal.is_trading_day(date(2027, 3, 26)), "Good Friday 2027 (Easter 2027-03-28)"
    assert not cal.is_trading_day(date(2027, 6, 18)), "Juneteenth observed (Jun 19 = Sat)"
    assert not cal.is_trading_day(date(2027, 7, 5)), "Independence Day observed (Jul 4 = Sun)"
    assert not cal.is_trading_day(date(2027, 12, 24)), "Christmas observed (Dec 25 = Sat)"
    # New Year's Day 2028 is a Saturday: NYSE does NOT close Fri 2027-12-31.
    assert cal.is_trading_day(date(2027, 12, 31))
    # Sanity: weekends.
    assert not cal.is_trading_day(date(2026, 9, 5))
    assert not cal.is_trading_day(date(2026, 9, 6))


def test_table_ends_after_2027_then_holidays_silently_become_trading_days():
    """Documented limitation, made explicit: nothing warns when the table
    runs out. MLK 2028 is a NYSE holiday but the calendar calls it open."""
    assert max(cal._HOLIDAYS).year == 2027
    mlk_2028 = _nth_weekday(2028, 1, 0, 3)
    assert cal.is_trading_day(mlk_2028)  # wrong once 2028 arrives — see F-list


# ---------------------------------------------------------------------------
# next_open around the 2026-09-07 Labor Day weekend (the Friday-close cycle)
# ---------------------------------------------------------------------------


def test_next_open_skips_labor_day_2026_from_friday_close():
    friday_1630 = datetime(2026, 9, 4, 16, 30, tzinfo=NY)   # pod-1 cron fire
    nxt = cal.next_open(friday_1630)
    assert nxt == datetime(2026, 9, 8, 9, 30, tzinfo=NY)
    assert nxt.astimezone(UTC) == datetime(2026, 9, 8, 13, 30, tzinfo=UTC)  # EDT


def test_next_open_skips_good_friday_2026():
    nxt = cal.next_open(datetime(2026, 4, 2, 17, 0, tzinfo=NY))
    assert nxt == datetime(2026, 4, 6, 9, 30, tzinfo=NY)


def test_next_open_is_strictly_after_at_exactly_0930():
    at_open = datetime(2026, 9, 8, 9, 30, 0, tzinfo=NY)
    assert cal.next_open(at_open) == datetime(2026, 9, 9, 9, 30, tzinfo=NY)
    just_before = at_open - timedelta(seconds=1)
    assert cal.next_open(just_before) == at_open


# ---------------------------------------------------------------------------
# DST: the open is 09:30 America/New_York, i.e. 13:30 UTC in summer and
# 14:30 UTC in winter. Everything must be computed in NY, never as a fixed
# UTC offset.
# ---------------------------------------------------------------------------


def test_next_open_winter_is_1430_utc():
    nxt = cal.next_open(datetime(2026, 1, 15, 20, 0, tzinfo=UTC))
    assert nxt.astimezone(UTC) == datetime(2026, 1, 16, 14, 30, tzinfo=UTC)


def test_next_open_summer_is_1330_utc():
    nxt = cal.next_open(datetime(2026, 7, 15, 20, 0, tzinfo=UTC))
    assert nxt.astimezone(UTC) == datetime(2026, 7, 16, 13, 30, tzinfo=UTC)


def test_next_open_across_spring_forward_2026():
    # DST starts Sun 2026-03-08. Friday 03-06 open = 14:30Z, Monday 03-09 open = 13:30Z.
    fri = cal.next_open(datetime(2026, 3, 5, 22, 0, tzinfo=UTC))
    mon = cal.next_open(datetime(2026, 3, 6, 22, 0, tzinfo=UTC))
    assert fri.astimezone(UTC) == datetime(2026, 3, 6, 14, 30, tzinfo=UTC)
    assert mon.astimezone(UTC) == datetime(2026, 3, 9, 13, 30, tzinfo=UTC)


def test_next_open_across_fall_back_2026():
    # DST ends Sun 2026-11-01. Fri 10-30 open = 13:30Z, Mon 11-02 open = 14:30Z.
    fri = cal.next_open(datetime(2026, 10, 29, 22, 0, tzinfo=UTC))
    mon = cal.next_open(datetime(2026, 10, 30, 22, 0, tzinfo=UTC))
    assert fri.astimezone(UTC) == datetime(2026, 10, 30, 13, 30, tzinfo=UTC)
    assert mon.astimezone(UTC) == datetime(2026, 11, 2, 14, 30, tzinfo=UTC)


def test_is_market_open_uses_ny_wall_clock_in_winter():
    # 13:45 UTC on a January trading day is 08:45 ET — still closed.
    assert cal.is_market_open(datetime(2026, 1, 16, 13, 45, tzinfo=UTC)) is False
    assert cal.is_market_open(datetime(2026, 1, 16, 14, 30, tzinfo=UTC)) is True
    assert cal.is_market_open(datetime(2026, 1, 16, 20, 59, tzinfo=UTC)) is True
    assert cal.is_market_open(datetime(2026, 1, 16, 21, 0, tzinfo=UTC)) is False


def test_is_market_open_uses_ny_wall_clock_in_summer():
    assert cal.is_market_open(datetime(2026, 7, 16, 13, 29, tzinfo=UTC)) is False
    assert cal.is_market_open(datetime(2026, 7, 16, 13, 30, tzinfo=UTC)) is True
    assert cal.is_market_open(datetime(2026, 7, 16, 20, 0, tzinfo=UTC)) is False


def test_release_after_from_friday_close_lands_on_tuesday_after_labor_day():
    """The exact pending_open scenario for the 2026-09-04 Friday cycles."""
    for fire in (
        datetime(2026, 9, 4, 16, 30, tzinfo=NY),
        datetime(2026, 9, 4, 16, 45, tzinfo=NY),
        datetime(2026, 9, 4, 17, 0, tzinfo=NY),
    ):
        assert cal.is_market_open(fire) is False
        assert cal.next_open(fire) == datetime(2026, 9, 8, 9, 30, tzinfo=NY)
    # The 60 s beat on Monday 2026-09-07 (holiday) must NOT release.
    assert cal.is_market_open(datetime(2026, 9, 7, 10, 0, tzinfo=NY)) is False


# ---------------------------------------------------------------------------
# DEFECT: 13:00 ET early closes are not modelled at all.
# ---------------------------------------------------------------------------

EARLY_CLOSES_1300_ET = [
    date(2025, 7, 3),    # day before Independence Day (Fri Jul 4)
    date(2025, 11, 28),  # day after Thanksgiving
    date(2025, 12, 24),  # Christmas Eve (Wed)
    date(2026, 11, 27),  # day after Thanksgiving
    date(2026, 12, 24),  # Christmas Eve (Thu)
    date(2027, 11, 26),  # day after Thanksgiving
]


@pytest.mark.parametrize("day", EARLY_CLOSES_1300_ET, ids=str)
def test_early_close_days_are_closed_after_1300_et(day):
    """FIXED (B3c): market_calendar models the 13:00 ET half-days, so
    is_market_open() reports CLOSED at 14:00 ET and nothing is released into
    a session the venue has already ended."""
    at_1400 = datetime(day.year, day.month, day.day, 14, 0, tzinfo=NY)
    assert cal.is_market_open(at_1400) is False
    assert cal.is_early_close(day) is True


@pytest.mark.parametrize("day", EARLY_CLOSES_1300_ET, ids=str)
def test_early_close_days_are_still_open_in_the_morning(day):
    at_1100 = datetime(day.year, day.month, day.day, 11, 0, tzinfo=NY)
    assert cal.is_market_open(at_1100) is True


def test_next_close_on_black_friday_2026_is_1300():
    """FIXED (B3c): next_close() honours the half-day table."""
    now = datetime(2026, 11, 27, 11, 0, tzinfo=NY)
    assert cal.next_close(now) == datetime(2026, 11, 27, 13, 0, tzinfo=NY)
    # …and from the previous close, the next close is that half-day's 13:00.
    prev_close = datetime(2026, 11, 25, 17, 0, tzinfo=NY)
    assert cal.next_close(prev_close) == datetime(2026, 11, 27, 13, 0, tzinfo=NY)


def test_full_days_still_close_at_1600():
    """Control: an ordinary session is untouched by the half-day table."""
    now = datetime(2026, 9, 8, 11, 0, tzinfo=NY)
    assert cal.is_early_close(now.date()) is False
    assert cal.next_close(now) == datetime(2026, 9, 8, 16, 0, tzinfo=NY)
    assert cal.is_market_open(datetime(2026, 9, 8, 15, 59, tzinfo=NY)) is True


def test_observed_holidays_are_never_half_days():
    """2026-07-03 is the *observed* Independence Day (a full close) and
    2027-12-24 the observed Christmas — neither is a 13:00 half-day."""
    assert cal.is_early_close(date(2026, 7, 3)) is False
    assert cal.is_early_close(date(2027, 12, 24)) is False
    assert cal.is_trading_day(date(2026, 7, 3)) is False
    assert cal.is_trading_day(date(2027, 12, 24)) is False


def test_half_day_table_covers_2028():
    """The half-day table runs a year past the holiday table (2025–2028)."""
    assert cal.is_early_close(date(2028, 11, 24))  # day after Thanksgiving
    assert cal.is_early_close(date(2028, 7, 3))    # Mon before Jul 4 (Tue)
    # Dec 24 2028 is a Sunday → not a half-day.
    assert cal.is_early_close(date(2028, 12, 24)) is False


def test_calendar_horizon_warning_fires_within_six_months_of_the_table_end():
    """The tables are static; nothing else notices when they run out."""
    # The first table to run out is the one that matters (holidays end 2027,
    # half-days 2028) — past 2027-12-31 the calendar starts calling MLK a
    # trading day.
    first_to_expire = min(max(cal._HOLIDAYS).year, max(cal._EARLY_CLOSES).year)
    assert first_to_expire == 2027
    assert cal.table_horizon_warning(date(2025, 1, 1)) is None
    assert cal.table_horizon_warning(date(2026, 9, 7)) is None      # today: fine
    assert cal.table_horizon_warning(date(first_to_expire, 12, 1)) is not None
    assert "refresh" in cal.table_horizon_warning(date(first_to_expire, 7, 15))
    assert "2027-12-31" in cal.table_horizon_warning(date(first_to_expire, 12, 31))
