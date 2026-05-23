"""Endpoint + capability-gating tests for the market screener (P3 prereq 3)."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.screener.capabilities import ScreenerCapability
from apps.screener.datasource import FmpScreenerDataSource
from apps.screener.models import SavedScreen, Watchlist, WatchlistItem


User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="alice@example.test", password="pass-123-fake-12345-x")


@pytest.fixture
def other_user(db):
    return User.objects.create_user(email="bob@example.test", password="pass-123-fake-12345-x")


@pytest.fixture
def auth_client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user=user)
    return c


@pytest.fixture
def stub_datasource(monkeypatch):
    class _Stub:
        provider_name = "stub"

        def capabilities(self):
            return frozenset(
                {
                    ScreenerCapability.BASIC_SCREEN,
                    ScreenerCapability.DAILY_BARS,
                    ScreenerCapability.INTRADAY_QUOTE,
                    ScreenerCapability.NEWS_CATALYST,
                }
            )

        def screen(self, params):
            return []

        def quotes(self, tickers):
            return {}

        def daily_bars(self, ticker, *, end, lookback_days):
            return []

    monkeypatch.setattr(
        "apps.screener.views.get_screener_datasource", lambda user: _Stub()
    )
    monkeypatch.setattr(
        "apps.screener.serializers.get_screener_datasource", lambda user: _Stub()
    )
    return _Stub


def test_fields_endpoint_marks_short_interest_unavailable(auth_client, stub_datasource):
    resp = auth_client.get("/api/screener/fields/")
    assert resp.status_code == 200
    payload = resp.json()
    short_interest = next(
        (f for f in payload["fields"] if f["id"] == "short_interest_pct"), None
    )
    assert short_interest is not None
    assert short_interest["available"] is False
    assert short_interest["requires"] == ["short_interest"]


def test_presets_endpoint_lists_shipping_presets(auth_client, stub_datasource):
    resp = auth_client.get("/api/screener/presets/")
    assert resp.status_code == 200
    presets = {p["id"]: p for p in resp.json()["presets"]}
    # Short Squeeze was removed at the user's request; the capability seam
    # for the `short_interest_pct` / `days_to_cover` fields is still
    # exercised by `test_run_endpoint_rejects_short_interest_criterion`.
    assert "short_squeeze" not in presets
    for pid in ("momentum", "gap", "gap_down", "large_caps", "stocks_in_play"):
        assert presets[pid]["available"] is True


def test_run_endpoint_rejects_short_interest_criterion(auth_client, stub_datasource):
    resp = auth_client.post(
        "/api/screener/run/",
        {
            "asset_class": "equity",
            "criteria": {"short_interest_pct": {"min": 20}},
            "sort": {"field": "short_interest_pct", "dir": "desc"},
        },
        format="json",
    )
    assert resp.status_code == 400
    assert "short_interest" in resp.json()["detail"]


def test_run_endpoint_runs_with_empty_candidates(auth_client, stub_datasource):
    resp = auth_client.post(
        "/api/screener/run/",
        {
            "asset_class": "all",
            "criteria": {"market_cap": {"min": 10_000_000_000}},
            "sort": {"field": "market_cap", "dir": "desc"},
        },
        format="json",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["returned_count"] == 0
    assert body["provider"] == "stub"
    assert "basic_screen" in body["capabilities"]


def test_run_rate_limit_429_on_second_call_inside_5s(auth_client, stub_datasource):
    body = {"asset_class": "equity", "criteria": {}, "sort": {"field": "market_cap", "dir": "desc"}}
    r1 = auth_client.post("/api/screener/run/", body, format="json")
    assert r1.status_code == 200
    r2 = auth_client.post("/api/screener/run/", body, format="json")
    assert r2.status_code == 429


def test_saved_screen_create_list_update_delete(auth_client, user, stub_datasource):
    create = auth_client.post(
        "/api/screener/saved/",
        {
            "name": "MyScreen",
            "asset_class": "equity",
            "filters": {"market_cap": {"min": 1_000_000_000}},
            "sort": {"field": "market_cap", "dir": "desc"},
        },
        format="json",
    )
    assert create.status_code == 201, create.content
    sid = create.json()["id"]

    duplicate = auth_client.post(
        "/api/screener/saved/",
        {
            "name": "MyScreen",
            "asset_class": "equity",
            "filters": {"market_cap": {"min": 1_000_000_000}},
            "sort": {"field": "market_cap", "dir": "desc"},
        },
        format="json",
    )
    assert duplicate.status_code == 400

    listed = auth_client.get("/api/screener/saved/")
    assert listed.status_code == 200
    assert any(r["id"] == sid for r in listed.json())

    deleted = auth_client.delete(f"/api/screener/saved/{sid}/")
    assert deleted.status_code == 204
    assert not SavedScreen.objects.filter(id=sid).exists()


def test_saved_screen_user_isolation(user, other_user, stub_datasource):
    own = SavedScreen.objects.create(
        user=user,
        name="Mine",
        asset_class="equity",
        filters={"market_cap": {"min": 1}},
        sort={"field": "market_cap", "dir": "desc"},
    )
    other = APIClient()
    other.force_authenticate(user=other_user)
    resp = other.get(f"/api/screener/saved/{own.id}/")
    assert resp.status_code == 404


def test_watchlist_add_idempotent_and_delete(auth_client, user, stub_datasource):
    r1 = auth_client.post("/api/screener/watchlist/", {"ticker": "aapl"}, format="json")
    assert r1.status_code == 201
    assert r1.json()["ticker"] == "AAPL"
    r2 = auth_client.post("/api/screener/watchlist/", {"ticker": "AAPL"}, format="json")
    assert r2.status_code == 201
    wl = Watchlist.objects.get(user=user)
    assert wl.items.filter(ticker="AAPL").count() == 1

    rm = auth_client.delete("/api/screener/watchlist/AAPL/")
    assert rm.status_code == 204
    assert wl.items.filter(ticker="AAPL").count() == 0


def test_fmp_screener_data_source_advertises_expected_capabilities():
    from apps.data.providers.fmp import FmpProvider

    p = FmpProvider(api_key="dummy")
    ds = FmpScreenerDataSource(p)
    caps = ds.capabilities()
    assert ScreenerCapability.BASIC_SCREEN in caps
    assert ScreenerCapability.DAILY_BARS in caps
    assert ScreenerCapability.INTRADAY_QUOTE in caps
    assert ScreenerCapability.NEWS_CATALYST in caps
    assert ScreenerCapability.SHORT_INTEREST not in caps
    assert ScreenerCapability.PREMARKET_VOLUME not in caps
