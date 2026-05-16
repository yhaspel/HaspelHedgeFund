"""No-cheat property test: after a graph run, no DailyBar / Fundamental
row whose dating is strictly after `as_of_date` may have been *read* by
the run. We exercise this by seeding contaminated rows and asserting
the run still ignores them.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.data.models import DailyBar, Fundamental
from apps.data.providers.fmp import SOURCE, FmpProvider


@pytest.mark.django_db
def test_provider_does_not_return_post_as_of_rows() -> None:
    as_of = dt.date(2024, 12, 31)
    DailyBar.objects.create(
        ticker="AAPL", date=as_of, open=1, high=1, low=1, close=1,
        adjusted_close=1, volume=1, source=SOURCE,
    )
    # "Future" leaked row — must NEVER appear in results.
    DailyBar.objects.create(
        ticker="AAPL", date=as_of + dt.timedelta(days=5),
        open=999, high=999, low=999, close=999,
        adjusted_close=999, volume=999, source=SOURCE,
    )
    Fundamental.objects.create(
        ticker="AAPL", as_of_date=as_of + dt.timedelta(days=30),
        period_end=dt.date(2024, 12, 31), metric="revenue",
        value=Decimal("999"), source=SOURCE,
    )
    Fundamental.objects.create(
        ticker="AAPL", as_of_date=as_of - dt.timedelta(days=10),
        period_end=dt.date(2024, 9, 30), metric="revenue",
        value=Decimal("100"), source=SOURCE,
    )

    with patch.object(FmpProvider, "_ensure_bars_cached", return_value=None), \
         patch.object(FmpProvider, "_ensure_statement_cached", return_value=None):
        provider = FmpProvider(api_key="fake")
        bars = provider.get_daily_bars(
            "AAPL", as_of - dt.timedelta(days=10), as_of + dt.timedelta(days=30), as_of=as_of
        )
        funds = provider.get_fundamentals("AAPL", ["revenue"], as_of=as_of)

    assert all(b.date <= as_of for b in bars), "bars leaked past as_of"
    assert all(f.as_of_date <= as_of for f in funds), "fundamentals leaked past as_of"
    assert all(f.value == Decimal("100") for f in funds)
