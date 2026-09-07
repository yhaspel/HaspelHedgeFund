"""Adversarial review (reviewer: data) — data endpoints: profile batch fan-out,
sparkline multi-source rows, profile cache sharing, regime batch bounds.

Proof tests for:
  * F-PROFILE-FANOUT: ``TickerProfileBatchView`` performs up to 50 x 2 FMP
    calls per request for unknown symbols; unresolvable symbols are never
    negatively cached, so the same request repeats the fan-out every time.
  * F-SPARK-SOURCE: ``TickerSparklineView`` does not filter ``DailyBar.source``
    — two providers for the same ticker yield duplicate dates.
  * F-PROFILE-CACHE-GLOBAL: ``profile:fmp:<SYM>`` Redis key is shared across
    users — user B (no FMP key) is served data fetched with user A's key.
  * F-REGIME-BATCH-UNBOUNDED: ``RegimeBatchView`` runs one query per ticker
    with no cap on the ticker list.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.data.models import DailyBar

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture(autouse=True)
def _clear_factory_caches():
    from apps.data.providers.factory import _reset_caches_for_tests

    _reset_caches_for_tests()
    yield
    _reset_caches_for_tests()


class _CountingFmp:
    """Stands in for FmpProvider: every profile lookup = 2 HTTP calls
    (/quote + /ratios-ttm) in the real adapter; nothing resolves."""

    def __init__(self) -> None:
        self.lookups: list[str] = []

    def get_quote_profile(self, sym):
        self.lookups.append(sym)
        return None


# ---------------------------------------------------------------------------
# F-PROFILE-FANOUT
# ---------------------------------------------------------------------------


def test_profile_batch_fans_out_50_lookups_per_request_and_never_caches_misses(monkeypatch):
    fake = _CountingFmp()
    monkeypatch.setattr("apps.data.providers.factory.get_fmp_provider", lambda user=None, **k: fake)
    user = User.objects.create_user(email="v1@x.com", password="x" * 12)
    client = APIClient()
    client.force_authenticate(user)
    symbols = ",".join(f"ZZ{i:03d}" for i in range(80))  # 80 garbage symbols
    for _ in range(3):
        resp = client.get("/api/tickers/profiles/", {"symbols": symbols})
        assert resp.status_code == 200
    # Capped at 50 per request, but repeated verbatim on every request:
    # 3 requests -> 150 profile lookups -> 300 FMP HTTP calls, zero results.
    assert len(fake.lookups) == 150


# ---------------------------------------------------------------------------
# F-SPARK-SOURCE
# ---------------------------------------------------------------------------


def test_sparkline_returns_duplicate_dates_when_two_sources_cached():
    d = dt.date(2026, 9, 4)
    for src, px in (("fmp", "100"), ("tiingo", "101")):
        DailyBar.objects.create(
            ticker="AAPL", date=d, open=1, high=1, low=1, close=Decimal(px),
            adjusted_close=Decimal(px), volume=1, source=src,
        )
    user = User.objects.create_user(email="v2@x.com", password="x" * 12)
    client = APIClient()
    client.force_authenticate(user)
    resp = client.get("/api/tickers/AAPL/sparkline/", {"as_of": "2026-09-07", "days": 30})
    bars = resp.json()["bars"]
    assert [b["date"] for b in bars] == [d.isoformat(), d.isoformat()]
    assert sorted(b["close"] for b in bars) == [100.0, 101.0]


# ---------------------------------------------------------------------------
# F-PROFILE-CACHE-GLOBAL
# ---------------------------------------------------------------------------


def test_profile_cache_is_shared_across_users(monkeypatch):
    from django.test import override_settings

    store: dict[str, object] = {}
    monkeypatch.setattr("apps.data.views.cache_get", lambda k: store.get(k))
    monkeypatch.setattr(
        "apps.data.views.cache_set",
        lambda k, v, ttl_seconds=0: store.__setitem__(k, v),
    )

    from apps.data.interfaces import ProfileSnapshot

    class _KeyedFmp:
        def get_quote_profile(self, sym):
            return ProfileSnapshot(
                ticker=sym, name="Apple Inc.", exchange="NASDAQ", sector="",
                price=Decimal("1"), market_cap=Decimal("2"), pe_ratio=None, eps=None,
                shares_outstanding=None, as_of=dt.date(2026, 9, 7),
            )

    resolved_for: list[int | None] = []

    def fake_factory(user=None, **_k):
        # Emulate BYOK: only user A has a key.
        resolved_for.append(getattr(user, "id", None))
        if getattr(user, "email", "") == "a@x.com":
            return _KeyedFmp()
        raise RuntimeError("No FMP key configured for this user.")

    monkeypatch.setattr("apps.data.providers.factory.get_fmp_provider", fake_factory)
    a = User.objects.create_user(email="a@x.com", password="x" * 12)
    b = User.objects.create_user(email="b@x.com", password="x" * 12)

    with override_settings(ALLOW_PLATFORM_DATA_KEYS=False):
        ca = APIClient()
        ca.force_authenticate(a)
        assert ca.get("/api/tickers/AAPL/profile/").json()["price"] == "1"
        cb = APIClient()
        cb.force_authenticate(b)
        body = cb.get("/api/tickers/AAPL/profile/").json()
    # User B has no key, yet receives the live quote fetched on A's key; the
    # factory (and hence the missing-key message) was never consulted for B.
    assert body["price"] == "1" and "detail" not in body
    assert resolved_for == [a.id]


# ---------------------------------------------------------------------------
# F-REGIME-BATCH-UNBOUNDED
# ---------------------------------------------------------------------------


def test_regime_batch_is_capped_at_50_tickers():
    """FIXED: the batch endpoint caps the fan-out (one DB query per ticker)
    at the same 50 the profile batch uses, and says so in the payload."""
    user = User.objects.create_user(email="v3@x.com", password="x" * 12)
    client = APIClient()
    client.force_authenticate(user)
    tickers = ",".join(f"T{i}" for i in range(500))
    with CaptureQueriesContext(connection) as ctx:
        resp = client.get("/api/macro/regime/batch/", {"tickers": tickers})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 50
    assert body["requested_count"] == 500
    assert body["truncated"] is True
    assert body["max_tickers"] == 50
    # 500 snapshot queries can no longer be triggered by one request.
    assert len(ctx.captured_queries) < 100


# ---------------------------------------------------------------------------
# F-OUTAGE-500 — provider outages surface as HTTP 500, not a degraded payload
# ---------------------------------------------------------------------------


def test_macro_snapshot_view_503s_when_fred_is_down(monkeypatch):
    """FIXED: a provider outage is a 503 with a readable ``detail``."""
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("fred unreachable")

    monkeypatch.setattr("hedgefund_agents.macro.macro_agent.compute_snapshot", boom)
    user = User.objects.create_user(email="o1@x.com", password="x" * 12)
    client = APIClient()
    client.force_authenticate(user)
    client.raise_request_exception = False
    resp = client.get("/api/macro/snapshot/")  # empty table -> inline prewarm
    assert resp.status_code == 503
    body = resp.json()
    assert "detail" in body and "unavailable" in body["detail"].lower()
    assert body["provider_error"] == "ConnectError"


def test_screener_run_503s_when_fmp_is_unreachable(monkeypatch):
    """FIXED: an FMP outage is a 503 with a readable ``detail``."""
    import httpx

    class _DS:
        provider_name = "fmp"

        def capabilities(self):
            from apps.screener.capabilities import ScreenerCapability as C

            return frozenset({C.BASIC_SCREEN})

        def screen(self, params):
            raise httpx.ConnectError("fmp unreachable")  # or a 429 / 5xx

    monkeypatch.setattr("apps.screener.views.get_screener_datasource", lambda user: _DS())
    monkeypatch.setattr("apps.screener.pipeline.cache_get", lambda k: None)
    user = User.objects.create_user(email="o2@x.com", password="x" * 12)
    client = APIClient()
    client.force_authenticate(user)
    client.raise_request_exception = False
    resp = client.post(
        "/api/screener/run/",
        {"asset_class": "equity", "criteria": {}, "sort": {"field": "market_cap", "dir": "desc"}},
        format="json",
    )
    assert resp.status_code == 503
    assert "unavailable" in resp.json()["detail"].lower()
