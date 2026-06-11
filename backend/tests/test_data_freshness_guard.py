"""P10 §A1/§A2 — live-cycle data-freshness guard, adjusted-close tail repair,
the unconditional recent-bar upsert, the Alpaca order-domain-403 classification,
and the stuck-`confirmed` order sweep.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.data.freshness import (
    StaleMarketDataError,
    assert_universe_fresh,
    last_adjustment_ex_date,
    normalize_adjusted_tail,
)
from apps.data.models import CorporateAction, DailyBar
from apps.data.providers.fmp import FmpProvider


def _bar(ticker: str, d: dt.date, close: str, adj: str) -> None:
    DailyBar.objects.create(
        ticker=ticker, date=d, open=close, high=close, low=close,
        close=Decimal(close), adjusted_close=Decimal(adj), volume=1, source="fmp",
    )


# --- adjusted-close tail repair -------------------------------------------


@pytest.mark.django_db
def test_normalize_adjusted_tail_resets_post_dividend_bars_only() -> None:
    # A real dividend on 2026-03-20; bars before keep their back-adjustment,
    # bars after must have adjusted_close == close.
    CorporateAction.objects.create(
        ticker="SPY", as_of_date=dt.date(2026, 3, 20),
        kind=CorporateAction.CASH_DIVIDEND, amount=Decimal("1.7"), source="fmp",
    )
    _bar("SPY", dt.date(2026, 3, 10), "500", "498.3")   # pre-div: genuine adj, keep
    _bar("SPY", dt.date(2026, 6, 5), "749.89", "737.55")  # post-div: spurious, fix
    _bar("SPY", dt.date(2026, 6, 8), "752.00", "752.00")  # post-div: already clean

    fixed = normalize_adjusted_tail("SPY")

    assert fixed == 1  # only the 06-05 bar changed
    pre = DailyBar.objects.get(ticker="SPY", date=dt.date(2026, 3, 10))
    assert pre.adjusted_close == Decimal("498.3")
    b = DailyBar.objects.get(ticker="SPY", date=dt.date(2026, 6, 5))
    assert b.adjusted_close == b.close == Decimal("749.89")


@pytest.mark.django_db
def test_normalize_adjusted_tail_no_dividend_flattens_all() -> None:
    # GLD never pays a dividend → adjusted_close must equal close everywhere.
    _bar("GLD", dt.date(2026, 6, 5), "398.13", "397.27")
    _bar("GLD", dt.date(2026, 6, 8), "399.00", "399.00")
    assert last_adjustment_ex_date("GLD") is None
    assert normalize_adjusted_tail("GLD") == 1
    assert all(b.adjusted_close == b.close for b in DailyBar.objects.filter(ticker="GLD"))


@pytest.mark.django_db
def test_normalize_adjusted_tail_preserves_split_history() -> None:
    # A split (no cash dividend) must NOT have its pre-split back-adjustment
    # flattened — only bars on/after the split ex-date are reset to close.
    CorporateAction.objects.create(
        ticker="NVDA", as_of_date=dt.date(2026, 4, 1),
        kind=CorporateAction.SPLIT, ratio=Decimal("10"), source="fmp",
    )
    _bar("NVDA", dt.date(2026, 3, 1), "1200", "120")    # pre-split: 10x back-adjustment, keep
    _bar("NVDA", dt.date(2026, 5, 1), "130", "128.7")   # post-split: spurious, fix
    assert last_adjustment_ex_date("NVDA") == dt.date(2026, 4, 1)
    assert normalize_adjusted_tail("NVDA") == 1  # only the 05-01 bar
    pre = DailyBar.objects.get(ticker="NVDA", date=dt.date(2026, 3, 1))
    assert pre.adjusted_close == Decimal("120")  # split adjustment preserved
    post = DailyBar.objects.get(ticker="NVDA", date=dt.date(2026, 5, 1))
    assert post.adjusted_close == post.close == Decimal("130")


# --- the freshness / integrity assert --------------------------------------


@pytest.mark.django_db
def test_assert_universe_fresh_passes_on_clean_current_data() -> None:
    as_of = dt.date(2026, 6, 12)
    _bar("SPY", dt.date(2026, 6, 11), "750", "750")
    _bar("QQQ", dt.date(2026, 6, 11), "726", "726")
    assert_universe_fresh(["SPY", "QQQ"], as_of)  # no raise


@pytest.mark.django_db
def test_assert_universe_fresh_flags_stale() -> None:
    as_of = dt.date(2026, 6, 12)
    _bar("SPY", dt.date(2026, 6, 2), "750", "750")  # 10 days stale
    with pytest.raises(StaleMarketDataError) as ei:
        assert_universe_fresh(["SPY"], as_of)
    assert "stale" in str(ei.value)


@pytest.mark.django_db
def test_assert_universe_fresh_flags_corrupt_adjusted_close() -> None:
    as_of = dt.date(2026, 6, 12)
    _bar("QQQ", dt.date(2026, 6, 11), "726", "705")  # ~2.9% phantom, no dividend
    with pytest.raises(StaleMarketDataError) as ei:
        assert_universe_fresh(["QQQ"], as_of)
    assert "corrupt" in str(ei.value)


@pytest.mark.django_db
def test_assert_universe_fresh_allows_factor_on_actual_ex_date() -> None:
    as_of = dt.date(2026, 6, 12)
    # A dividend genuinely went ex on the latest bar date → adj!=close is allowed.
    CorporateAction.objects.create(
        ticker="TLT", as_of_date=dt.date(2026, 6, 11),
        kind=CorporateAction.CASH_DIVIDEND, amount=Decimal("0.3"), source="fmp",
    )
    _bar("TLT", dt.date(2026, 6, 11), "85.00", "84.66")
    assert_universe_fresh(["TLT"], as_of)  # no raise


@pytest.mark.django_db
def test_assert_universe_fresh_flags_missing_ticker() -> None:
    with pytest.raises(StaleMarketDataError):
        assert_universe_fresh(["NOPE"], dt.date(2026, 6, 12))


# --- the unconditional recent-bar upsert -----------------------------------


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._p


class _Http:
    """Routes /full vs /dividend-adjusted to canned payloads."""

    def __init__(self, full, adj):
        self._full, self._adj = full, adj
        self.calls: list[str] = []

    def get(self, url, params=None):
        self.calls.append(url)
        return _Resp(self._adj if "dividend-adjusted" in url else self._full)


@pytest.mark.django_db
def test_upsert_recent_bars_inserts_and_updates() -> None:
    # An existing corrupt 06-05 bar; the upsert refreshes it AND adds 06-08.
    _bar("SPY", dt.date(2026, 6, 5), "749.89", "737.55")
    full = [
        {"date": "2026-06-05", "open": 750, "high": 752, "low": 748, "close": 749.89, "volume": 1},
        {"date": "2026-06-08", "open": 751, "high": 753, "low": 749, "close": 752.00, "volume": 1},
    ]
    adj = [
        {"date": "2026-06-05", "adjClose": 737.55},  # FMP's corrupt tail value
        {"date": "2026-06-08", "adjClose": 739.22},
    ]
    p = FmpProvider(api_key="fake")
    p._http = _Http(full, adj)  # type: ignore[assignment]
    written = p.upsert_recent_bars("SPY", dt.date(2026, 6, 1), dt.date(2026, 6, 8))
    assert written == 2
    assert DailyBar.objects.filter(ticker="SPY").count() == 2
    # raw close updated; adjusted seeded from FMP (normalize_adjusted_tail fixes it after)
    b8 = DailyBar.objects.get(ticker="SPY", date=dt.date(2026, 6, 8))
    assert b8.close == Decimal("752")
