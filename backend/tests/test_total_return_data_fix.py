"""Tests for the total-return data fix: FMP dividend / adjusted-close ingestion
and the backfill commands that make backtests credit dividends instead of
running price-only. See development-plans/plan-reviews/research_trend-cta-...md.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.backtests.corporate_actions import actions_on
from apps.data.models import CorporateAction, DailyBar
from apps.data.providers.fmp import FmpProvider


class _FakeResp:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class _FakeHttp:
    """Stands in for the provider's httpx.Client — returns a canned payload."""

    def __init__(self, payload: object) -> None:
        self._payload = payload
        self.calls: list[tuple] = []

    def get(self, url: str, params: dict | None = None) -> _FakeResp:
        self.calls.append((url, params))
        return _FakeResp(self._payload)


def test_get_dividends_parses_filters_and_skips_bad_rows() -> None:
    payload = [
        {"symbol": "TLT", "date": "2024-06-03", "dividend": 0.30, "adjDividend": 0.30},
        {"symbol": "TLT", "date": "2020-01-02", "dividend": 0.25},
        {"symbol": "TLT", "date": "2014-12-31", "dividend": 0.20},  # before start → excluded
        {"symbol": "TLT", "date": "2024-07-01", "dividend": 0.0},   # zero → excluded
        {"symbol": "TLT", "date": "not-a-date", "dividend": 0.1},   # unparseable → excluded
        {"symbol": "TLT", "dividend": 0.1},                          # no date → excluded
    ]
    p = FmpProvider(api_key="fake")
    p._http = _FakeHttp(payload)  # type: ignore[assignment]
    out = p.get_dividends("TLT", dt.date(2015, 1, 1), dt.date(2025, 1, 1))
    assert (dt.date(2024, 6, 3), Decimal("0.30")) in out
    assert (dt.date(2020, 1, 2), Decimal("0.25")) in out
    assert len(out) == 2  # the four bad/out-of-range rows are dropped
    # hit the dividends endpoint with the symbol param
    assert p._http.calls[0][1]["symbol"] == "TLT"  # type: ignore[attr-defined]


def test_get_adjusted_closes_maps_date_to_adjclose() -> None:
    payload = [
        {"symbol": "SPY", "date": "2024-06-03", "adjClose": 500.5},
        {"symbol": "SPY", "date": "2024-06-04", "adjClose": 501.0},
        {"symbol": "SPY", "date": "2024-06-05"},  # missing adjClose → skipped
    ]
    p = FmpProvider(api_key="fake")
    p._http = _FakeHttp(payload)  # type: ignore[assignment]
    out = p.get_adjusted_closes("SPY", dt.date(2024, 1, 1), dt.date(2024, 12, 31))
    assert out == {"2024-06-03": "500.5", "2024-06-04": "501.0"}


@pytest.mark.django_db
def test_actions_on_returns_dividend_from_corporate_action() -> None:
    """The fix relies on actions_on surfacing CorporateAction dividend rows so the
    engine's apply_dividend credits cash (total-return)."""
    CorporateAction.objects.create(
        ticker="TLT", as_of_date=dt.date(2024, 6, 3),
        kind=CorporateAction.CASH_DIVIDEND, amount=Decimal("0.33"), source="fmp",
    )
    assert actions_on("TLT", dt.date(2024, 6, 3)) == [{"kind": "dividend", "dps": 0.33}]
    # No row + no bars on another date → legacy fallback yields nothing.
    assert actions_on("TLT", dt.date(2024, 6, 4)) == []


@pytest.mark.django_db
def test_backfill_dividends_creates_rows_and_is_idempotent(monkeypatch) -> None:
    from django.core.management import call_command

    import apps.data.management.commands.backfill_dividends as cmd

    DailyBar.objects.create(
        ticker="TLT", date=dt.date(2024, 1, 2), open=1, high=1, low=1, close=1,
        adjusted_close=1, volume=1, source="fmp",
    )

    class _StubProvider:
        def get_dividends(self, ticker, start, end):
            return [(dt.date(2024, 6, 3), Decimal("0.33")), (dt.date(2024, 9, 3), Decimal("0.34"))]

    monkeypatch.setattr(cmd, "get_fmp_provider", lambda **kw: _StubProvider())
    call_command("backfill_dividends", "--tickers", "TLT")
    q = CorporateAction.objects.filter(ticker="TLT", kind=CorporateAction.CASH_DIVIDEND)
    assert q.count() == 2
    # Re-run must not duplicate (unique_together + ignore_conflicts).
    call_command("backfill_dividends", "--tickers", "TLT")
    assert q.count() == 2


@pytest.mark.django_db
def test_backfill_adjusted_close_updates_and_preserves_raw_close(monkeypatch) -> None:
    from django.core.management import call_command

    import apps.data.management.commands.backfill_adjusted_close as cmd

    DailyBar.objects.create(
        ticker="SPY", date=dt.date(2024, 1, 2), open=1, high=1, low=1,
        close=Decimal("100"), adjusted_close=Decimal("100"), volume=1, source="fmp",
    )

    class _Stub:
        def get_adjusted_closes(self, ticker, start, end):
            return {"2024-01-02": "70.5"}

    monkeypatch.setattr(cmd, "get_fmp_provider", lambda **kw: _Stub())
    call_command("backfill_adjusted_close", "--tickers", "SPY")
    b = DailyBar.objects.get(ticker="SPY", date=dt.date(2024, 1, 2))
    assert b.adjusted_close == Decimal("70.5")  # dividend-adjusted
    assert b.close == Decimal("100")            # raw close untouched (fills stay correct)
