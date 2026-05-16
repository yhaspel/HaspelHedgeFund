"""Unit tests for FMP data_provider — including the no-cheat property."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.data.models import DailyBar, Fundamental
from apps.data.providers.fmp import SOURCE, FmpProvider


@pytest.mark.django_db
def test_get_daily_bars_respects_as_of() -> None:
    # Seed bars spanning before and after the as_of cutoff.
    for d in (dt.date(2024, 12, 30), dt.date(2024, 12, 31), dt.date(2025, 1, 2)):
        DailyBar.objects.create(
            ticker="AAPL", date=d, open=1, high=1, low=1, close=1,
            adjusted_close=1, volume=1, source=SOURCE,
        )
    provider = FmpProvider(api_key="fake")
    # Pretend we already cached everything so no HTTP call happens.
    provider._ensure_bars_cached = lambda *a, **kw: None  # type: ignore[method-assign]
    bars = provider.get_daily_bars(
        "AAPL",
        dt.date(2024, 12, 1),
        dt.date(2025, 1, 31),
        as_of=dt.date(2024, 12, 31),
    )
    dates = [b.date for b in bars]
    assert dates == [dt.date(2024, 12, 30), dt.date(2024, 12, 31)]
    assert dt.date(2025, 1, 2) not in dates  # no cheat


@pytest.mark.django_db
def test_get_fundamentals_respects_as_of() -> None:
    Fundamental.objects.create(
        ticker="AAPL", as_of_date=dt.date(2024, 11, 1),
        period_end=dt.date(2024, 9, 30), metric="revenue",
        value=Decimal("100"), source=SOURCE,
    )
    Fundamental.objects.create(
        ticker="AAPL", as_of_date=dt.date(2025, 2, 1),
        period_end=dt.date(2024, 12, 31), metric="revenue",
        value=Decimal("110"), source=SOURCE,
    )
    provider = FmpProvider(api_key="fake")
    provider._ensure_statement_cached = lambda *a, **kw: None  # type: ignore[method-assign]
    rows = provider.get_fundamentals(
        "AAPL", ["revenue"], as_of=dt.date(2024, 12, 31)
    )
    assert len(rows) == 1
    assert rows[0].value == Decimal("100")
