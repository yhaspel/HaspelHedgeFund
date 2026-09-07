"""WAVE-3 P2 item 3 — bounded screener fan-out, shared rate limiter, 503s.

The flipped proof test
(``test_review_data_screener.py::test_one_screen_run_issues_600_plus_fmp_calls_on_cold_cache``)
asserts the cold-cache bound. This file covers the warm path (zero HTTP calls
when ``DailyBar`` already has the history), the per-user limiter now living in
``django.core.cache``, and the provider-outage status code.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import MagicMock

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient

from apps.data.models import DailyBar
from apps.screener.capabilities import ScreenerCapability
from apps.screener.datasource import FmpScreenerDataSource, ScreenerDataSource

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _seed_bars(ticker: str, *, n: int = 300, end: dt.date | None = None) -> None:
    end = end or dt.date.today()
    rows = []
    d = end
    made = 0
    while made < n:
        if d.weekday() < 5:
            px = Decimal(100 + made)
            rows.append(
                DailyBar(
                    ticker=ticker, date=d, open=px, high=px, low=px, close=px,
                    adjusted_close=px, volume=1_000_000, source="fmp",
                )
            )
            made += 1
        d -= dt.timedelta(days=1)
    DailyBar.objects.bulk_create(rows)


class _CountingHttp:
    def __init__(self, screener_rows):
        self.calls: list[str] = []
        self._rows = screener_rows

    def get(self, url, params=None):
        self.calls.append(url)
        rows = self._rows

        class _R:
            def raise_for_status(self):
                return None

            def json(self):
                if url.endswith("/company-screener"):
                    return rows
                return []

        return _R()


def test_warm_cache_run_issues_no_bar_calls_at_all():
    """Every candidate's history is already in ``DailyBar`` → stage 2 makes
    exactly zero bar requests, and every row is marked ``cache``."""
    from apps.data.providers.fmp import FmpProvider
    from apps.screener.pipeline import run_screen

    tickers = [f"T{i:02d}" for i in range(5)]
    for t in tickers:
        _seed_bars(t)
    http = _CountingHttp([
        {"symbol": t, "companyName": t, "marketCap": 1e9, "price": 10,
         "volume": 1_000_000, "isEtf": False}
        for t in tickers
    ])
    ds = FmpScreenerDataSource(FmpProvider(api_key="k", http=http))  # type: ignore[arg-type]
    filters = {
        "asset_class": "equity", "criteria": {},
        "sort": {"field": "market_cap", "dir": "desc"}, "limit": 50,
    }
    result = run_screen(filters, user=None, datasource=ds, use_cache=False)

    assert [c for c in http.calls if "historical-price-eod" in c] == []
    assert {r.enrichment for r in result.rows} == {"cache"}
    # ... and the metrics really were computed off the cached bars.
    assert all(r.momentum_3m is not None for r in result.rows)
    assert any("5 from cache, 0 fetched, 0 not enriched" in w for w in result.warnings)


def test_cached_daily_bars_is_one_db_read_and_never_http():
    from apps.data.providers.fmp import FmpProvider

    _seed_bars("AAA", n=80)
    http = MagicMock()
    http.get.side_effect = AssertionError("cached_daily_bars must not do HTTP")
    ds = FmpScreenerDataSource(FmpProvider(api_key="k", http=http))
    out = ds.cached_daily_bars(
        ["AAA", "BBB"], end=dt.date.today(), lookback_days=380
    )
    assert len(out["AAA"]) == 80
    assert "BBB" not in out
    assert http.get.call_count == 0


def test_base_datasource_cached_bars_defaults_to_empty():
    assert ScreenerDataSource().cached_daily_bars(
        ["X"], end=dt.date.today(), lookback_days=380
    ) == {}


# ---------------------------------------------------------------------------
# Rate limiter now lives in django.core.cache (shared across processes)
# ---------------------------------------------------------------------------


class _EmptyDataSource(ScreenerDataSource):
    provider_name = "fmp"

    def capabilities(self):
        return frozenset({ScreenerCapability.BASIC_SCREEN})

    def screen(self, params):
        return []


class _BrokenDataSource(_EmptyDataSource):
    def screen(self, params):
        raise httpx.ConnectError("FMP unreachable")


def _auth_client(email="scr@x.com"):
    user = User.objects.create_user(email=email, password="x" * 12)
    client = APIClient()
    client.force_authenticate(user)
    return client, user


def _run_body():
    return {
        "asset_class": "equity", "filters": {},
        "sort": {"field": "market_cap", "dir": "desc"}, "limit": 10,
    }


def test_run_rate_limit_key_is_in_the_shared_cache(monkeypatch):
    monkeypatch.setattr(
        "apps.screener.views.get_screener_datasource", lambda user: _EmptyDataSource()
    )
    client, user = _auth_client()
    assert client.post("/api/screener/run/", _run_body(), format="json").status_code == 200
    # The limiter is a cache key, not per-process state — a second worker
    # (i.e. anything reading the same cache) sees it.
    from apps.screener.views import _rate_limit_key

    assert cache.get(_rate_limit_key(user.id)) is not None
    resp = client.post("/api/screener/run/", _run_body(), format="json")
    assert resp.status_code == 429
    cache.delete(_rate_limit_key(user.id))
    assert client.post("/api/screener/run/", _run_body(), format="json").status_code == 200


def test_provider_transport_error_is_503_with_detail(monkeypatch):
    monkeypatch.setattr(
        "apps.screener.views.get_screener_datasource", lambda user: _BrokenDataSource()
    )
    client, _user = _auth_client("scr503@x.com")
    resp = client.post("/api/screener/run/", _run_body(), format="json")
    assert resp.status_code == 503
    assert "unavailable" in resp.json()["detail"]
    assert "ConnectError" in resp.json()["detail"]


def test_run_payload_carries_enrichment_counts(monkeypatch):
    monkeypatch.setattr(
        "apps.screener.views.get_screener_datasource", lambda user: _EmptyDataSource()
    )
    client, _user = _auth_client("scrcounts@x.com")
    body = client.post("/api/screener/run/", _run_body(), format="json").json()
    assert body["enrichment_counts"] == {"cache": 0, "fetched": 0, "partial": 0}


@override_settings(SCREENER_LAZY_FILL_MAX="not-a-number")
def test_bad_lazy_fill_setting_falls_back_to_the_default():
    from apps.screener.pipeline import SCREENER_LAZY_FILL_MAX_DEFAULT, lazy_fill_max

    assert lazy_fill_max() == SCREENER_LAZY_FILL_MAX_DEFAULT
