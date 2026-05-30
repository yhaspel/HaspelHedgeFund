"""API + service tests for named watchlists (P3b)."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.watchlists.models import Watchlist, WatchlistTicker
from apps.watchlists.services import (
    add_tickers_to_watchlist,
    get_default_watchlist,
    watchlist_tickers,
)

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="wl-alice@example.test", password="pass-123-fake-12345-x"
    )


@pytest.fixture
def other_user(db):
    return User.objects.create_user(
        email="wl-bob@example.test", password="pass-123-fake-12345-x"
    )


@pytest.fixture
def auth_client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user=user)
    return c


@pytest.fixture(autouse=True)
def _no_datasource(monkeypatch):
    """Detail enrichment must not hit FMP in tests — force the bare-rows path."""
    def _raise(_user):
        raise RuntimeError("no datasource in tests")

    monkeypatch.setattr("apps.watchlists.views.get_screener_datasource", _raise)


# ---------- list CRUD ----------


def test_first_list_is_default_second_is_not(auth_client):
    r1 = auth_client.post("/api/watchlists/", {"name": "Quality compounders"}, format="json")
    assert r1.status_code == 201, r1.json()
    assert r1.json()["is_default"] is True
    assert r1.json()["ticker_count"] == 0

    r2 = auth_client.post("/api/watchlists/", {"name": "Macro plays"}, format="json")
    assert r2.status_code == 201
    assert r2.json()["is_default"] is False

    lst = auth_client.get("/api/watchlists/")
    assert lst.status_code == 200
    names = [w["name"] for w in lst.json()]
    assert "Quality compounders" in names and "Macro plays" in names


def test_duplicate_name_rejected(auth_client):
    auth_client.post("/api/watchlists/", {"name": "Dup"}, format="json")
    r = auth_client.post("/api/watchlists/", {"name": "Dup"}, format="json")
    assert r.status_code == 400


def test_rename_list_and_name_clash(auth_client):
    a = auth_client.post("/api/watchlists/", {"name": "A"}, format="json").json()
    auth_client.post("/api/watchlists/", {"name": "B"}, format="json")
    # rename A -> C
    r = auth_client.patch(f"/api/watchlists/{a['id']}/", {"name": "C"}, format="json")
    assert r.status_code == 200
    assert r.json()["name"] == "C"
    # rename C -> B clashes
    r2 = auth_client.patch(f"/api/watchlists/{a['id']}/", {"name": "B"}, format="json")
    assert r2.status_code == 400


def test_delete_default_promotes_oldest_remaining(auth_client, user):
    first = auth_client.post("/api/watchlists/", {"name": "First"}, format="json").json()
    auth_client.post("/api/watchlists/", {"name": "Second"}, format="json")
    assert first["is_default"] is True

    r = auth_client.delete(f"/api/watchlists/{first['id']}/")
    assert r.status_code == 204
    # The remaining list is now the default.
    remaining = Watchlist.objects.filter(user=user)
    assert remaining.count() == 1
    assert remaining.first().is_default is True


def test_max_lists_cap(auth_client, monkeypatch):
    monkeypatch.setattr("apps.watchlists.views.MAX_WATCHLISTS_PER_USER", 2)
    auth_client.post("/api/watchlists/", {"name": "one"}, format="json")
    auth_client.post("/api/watchlists/", {"name": "two"}, format="json")
    r = auth_client.post("/api/watchlists/", {"name": "three"}, format="json")
    assert r.status_code == 400


# ---------- tickers ----------


def test_add_remove_ticker_via_default_alias(auth_client, user):
    r1 = auth_client.post(
        "/api/watchlists/default/tickers/", {"ticker": "aapl"}, format="json"
    )
    assert r1.status_code == 201
    assert r1.json()["ticker"] == "AAPL"
    # idempotent
    r2 = auth_client.post(
        "/api/watchlists/default/tickers/", {"ticker": "AAPL"}, format="json"
    )
    assert r2.status_code == 201
    wl = get_default_watchlist(user)
    assert wl.tickers.filter(ticker="AAPL").count() == 1

    rm = auth_client.delete("/api/watchlists/default/tickers/AAPL/")
    assert rm.status_code == 204
    assert wl.tickers.filter(ticker="AAPL").count() == 0


def test_remove_missing_ticker_404(auth_client):
    r = auth_client.delete("/api/watchlists/default/tickers/ZZZZ/")
    assert r.status_code == 404


def test_ticker_cap(auth_client, user, monkeypatch):
    wl = get_default_watchlist(user)
    WatchlistTicker.objects.bulk_create(
        [WatchlistTicker(watchlist=wl, ticker=f"T{i:03d}") for i in range(100)]
    )
    r = auth_client.post(
        "/api/watchlists/default/tickers/", {"ticker": "NEW"}, format="json"
    )
    assert r.status_code == 400


def test_detail_returns_bare_rows(auth_client, user):
    wl = get_default_watchlist(user)
    WatchlistTicker.objects.create(watchlist=wl, ticker="NVDA", note="chip play")
    r = auth_client.get("/api/watchlists/default/")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "My Watchlist"
    assert body["items"][0]["ticker"] == "NVDA"
    assert body["items"][0]["note"] == "chip play"
    assert body["items"][0]["price"] is None  # bare row (no datasource)


# ---------- isolation ----------


def test_user_cannot_touch_others_list(auth_client, other_user):
    other = Watchlist.objects.create(user=other_user, name="Bob", is_default=True)
    r = auth_client.get(f"/api/watchlists/{other.id}/")
    assert r.status_code == 404
    r2 = auth_client.post(
        f"/api/watchlists/{other.id}/tickers/", {"ticker": "AAPL"}, format="json"
    )
    assert r2.status_code == 404


# ---------- service helpers ----------


def test_watchlist_tickers_union(user):
    a = Watchlist.objects.create(user=user, name="A", is_default=True)
    b = Watchlist.objects.create(user=user, name="B")
    WatchlistTicker.objects.create(watchlist=a, ticker="AAPL")
    WatchlistTicker.objects.create(watchlist=b, ticker="MSFT")
    WatchlistTicker.objects.create(watchlist=b, ticker="aapl".upper())
    assert watchlist_tickers(user) == {"AAPL", "MSFT"}


def test_add_tickers_targets_default(user):
    added = add_tickers_to_watchlist(user, ["AAPL", "msft", "AAPL"])
    assert added == 2
    wl = get_default_watchlist(user)
    assert sorted(wl.tickers.values_list("ticker", flat=True)) == ["AAPL", "MSFT"]


def test_get_default_promotes_existing(user):
    # A user with one non-default list should have it promoted, not a 2nd created.
    Watchlist.objects.create(user=user, name="Only")
    wl = get_default_watchlist(user)
    assert wl.name == "Only"
    assert wl.is_default is True
    assert Watchlist.objects.filter(user=user).count() == 1
