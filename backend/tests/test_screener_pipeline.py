"""Pipeline + validation tests for the market screener (P3 prereq 3).

These tests stub out the ``ScreenerDataSource`` so the pipeline can run
without hitting FMP.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.data.interfaces import Bar, QuoteSnapshot, ScreenerRow
from apps.screener.capabilities import ScreenerCapability
from apps.screener.datasource import ScreenerDataSource
from apps.screener.pipeline import (
    MAX_ENRICH,
    ScreenerValidationError,
    run_screen,
    validate_filters,
)


class StubDataSource(ScreenerDataSource):
    provider_name = "stub"

    def __init__(
        self,
        candidates: list[ScreenerRow],
        quotes: dict[str, QuoteSnapshot] | None = None,
        bars: list[Bar] | None = None,
        caps: frozenset | None = None,
    ) -> None:
        self._candidates = candidates
        self._quotes = quotes or {}
        self._bars = bars or []
        self._caps = caps or frozenset(
            {
                ScreenerCapability.BASIC_SCREEN,
                ScreenerCapability.DAILY_BARS,
                ScreenerCapability.INTRADAY_QUOTE,
                ScreenerCapability.NEWS_CATALYST,
            }
        )

    def capabilities(self) -> frozenset:
        return self._caps

    def screen(self, params):
        return list(self._candidates)

    def quotes(self, tickers):
        return {t.upper(): self._quotes[t.upper()] for t in tickers if t.upper() in self._quotes}

    def daily_bars(self, ticker, *, end, lookback_days):
        return list(self._bars)


def _row(ticker: str, *, mc: int, price: float, volume: int) -> ScreenerRow:
    return ScreenerRow(
        ticker=ticker,
        name=f"{ticker} Inc.",
        market_cap=Decimal(str(mc)),
        price=Decimal(str(price)),
        volume=volume,
        beta=Decimal("1.0"),
        sector="Technology",
        industry="Software",
        exchange="NASDAQ",
        country="US",
        is_etf=False,
        is_fund=False,
        last_annual_dividend=None,
    )


def _quote(
    ticker: str,
    *,
    price: float,
    prev: float,
    vol: int,
    year_high: float = 100,
    year_low: float = 50,
) -> QuoteSnapshot:
    return QuoteSnapshot(
        ticker=ticker,
        price=Decimal(str(price)),
        open=Decimal(str(price)),
        previous_close=Decimal(str(prev)),
        day_high=Decimal(str(price)),
        day_low=Decimal(str(price)),
        year_high=Decimal(str(year_high)),
        year_low=Decimal(str(year_low)),
        price_avg_50=Decimal(str(price - 1)),
        price_avg_200=Decimal(str(price - 2)),
        volume=vol,
        change_pct=Decimal(str((price / prev - 1) * 100)) if prev else None,
        market_cap=None,
        pe_ratio=None,
        eps=None,
        as_of=dt.date.today(),
    )


def test_validate_filters_rejects_unknown_field() -> None:
    with pytest.raises(ScreenerValidationError):
        validate_filters(
            {"asset_class": "equity", "criteria": {"bogus": {"min": 1}}},
            available_capabilities=frozenset({ScreenerCapability.BASIC_SCREEN}),
        )


def test_validate_filters_capability_gating_for_short_interest() -> None:
    with pytest.raises(ScreenerValidationError) as exc:
        validate_filters(
            {
                "asset_class": "equity",
                "criteria": {"short_interest_pct": {"min": 20}},
            },
            available_capabilities=frozenset({ScreenerCapability.BASIC_SCREEN}),
        )
    assert "short_interest" in str(exc.value)


def test_validate_filters_rejects_inverted_range() -> None:
    with pytest.raises(ScreenerValidationError):
        validate_filters(
            {
                "asset_class": "equity",
                "criteria": {"market_cap": {"min": 1_000_000_000, "max": 10}},
            },
            available_capabilities=frozenset({ScreenerCapability.BASIC_SCREEN}),
        )


def test_validate_filters_normalizes_sort_default() -> None:
    out = validate_filters(
        {"asset_class": "equity", "criteria": {}},
        available_capabilities=frozenset({ScreenerCapability.BASIC_SCREEN}),
    )
    assert out["sort"] == {"field": "market_cap", "dir": "desc"}
    assert out["limit"] == 200


@pytest.mark.django_db
def test_pipeline_runs_with_stub_and_sorts_descending() -> None:
    rows = [
        _row("AAA", mc=100, price=10, volume=1000),
        _row("BBB", mc=300, price=10, volume=1000),
        _row("CCC", mc=200, price=10, volume=1000),
    ]
    ds = StubDataSource(
        candidates=rows,
        quotes={
            "AAA": _quote("AAA", price=10, prev=10, vol=1000),
            "BBB": _quote("BBB", price=10, prev=10, vol=1000),
            "CCC": _quote("CCC", price=10, prev=10, vol=1000),
        },
    )
    filters = validate_filters(
        {"asset_class": "equity", "criteria": {}, "sort": {"field": "market_cap", "dir": "desc"}},
        available_capabilities=ds.capabilities(),
    )
    result = run_screen(filters, user=None, datasource=ds, use_cache=False)
    assert [r.ticker for r in result.rows] == ["BBB", "CCC", "AAA"]
    assert result.returned_count == 3
    assert result.provider == "stub"


@pytest.mark.django_db
def test_pipeline_truncates_when_universe_exceeds_max_enrich() -> None:
    rows = [
        _row(f"T{i:04d}", mc=1_000 - i, price=10, volume=10_000 - i)
        for i in range(MAX_ENRICH + 50)
    ]
    ds = StubDataSource(candidates=rows, quotes={})
    filters = validate_filters(
        {"asset_class": "equity", "criteria": {}},
        available_capabilities=ds.capabilities(),
    )
    result = run_screen(filters, user=None, datasource=ds, use_cache=False)
    assert result.truncated is True
    assert result.enriched_count == MAX_ENRICH
    assert any("most liquid" in w for w in result.warnings)


@pytest.mark.django_db
def test_pipeline_stage3_post_filter_drops_non_matching_rows() -> None:
    rows = [
        _row("HIGH", mc=100, price=10, volume=1000),
        _row("LOW", mc=100, price=10, volume=1000),
    ]
    quotes = {
        "HIGH": _quote("HIGH", price=110, prev=100, vol=1000),
        "LOW": _quote("LOW", price=95, prev=100, vol=1000),
    }
    ds = StubDataSource(candidates=rows, quotes=quotes)
    filters = validate_filters(
        {
            "asset_class": "equity",
            "criteria": {"change_pct": {"min": 5}},
            "sort": {"field": "change_pct", "dir": "desc"},
        },
        available_capabilities=ds.capabilities(),
    )
    result = run_screen(filters, user=None, datasource=ds, use_cache=False)
    assert [r.ticker for r in result.rows] == ["HIGH"]
