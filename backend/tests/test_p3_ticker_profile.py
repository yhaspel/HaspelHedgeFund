"""P3 prereq 2 (WS-2) — CompanyProfile / FmpProvider.get_quote_profile /
TickerProfileView / TickerProfileBatchView tests."""
from __future__ import annotations

import datetime as dt
import subprocess
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient

from apps.data.models import CompanyProfile
from apps.data.providers.fmp import FmpProvider

User = get_user_model()


@pytest.fixture(autouse=True)
def _clear_factory_caches():
    """The factory uses lru_cache; tests can leak state. Clear before/after."""
    from apps.data.providers.factory import _reset_caches_for_tests
    _reset_caches_for_tests()
    yield
    _reset_caches_for_tests()


def _quote_payload(**overrides: Any) -> list[dict]:
    """FMP stable /quote response — name, price, market cap. No P/E/EPS."""
    row = {
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "price": 234.55,
        "marketCap": 3500000000000,
        "exchange": "NASDAQ",
        "timestamp": 1734566400,
    }
    row.update(overrides)
    return [row]


def _ratios_payload(**overrides: Any) -> list[dict]:
    """FMP stable /ratios-ttm response — carries P/E and trailing EPS."""
    row = {
        "symbol": "AAPL",
        "priceToEarningsRatioTTM": 38.2,
        "netIncomePerShareTTM": 6.14,
    }
    row.update(overrides)
    return [row]


def _build_provider(*, quote: Any = None, ratios: Any = None) -> FmpProvider:
    """FmpProvider whose mock HTTP routes by endpoint: /ratios-ttm gets the
    ``ratios`` payload, /quote gets the ``quote`` payload."""

    def _get(url: str, params: Any = None) -> Any:
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = ratios if "ratios-ttm" in url else quote
        return resp

    mock_http = MagicMock()
    mock_http.get.side_effect = _get
    return FmpProvider(api_key="fake", http=mock_http)


# ---------------------------------------------------------------------------
# 1. FmpProvider.get_quote_profile field mapping.
# ---------------------------------------------------------------------------


def test_get_quote_profile_maps_fmp_quote_fields() -> None:
    provider = _build_provider(quote=_quote_payload(), ratios=_ratios_payload())
    snap = provider.get_quote_profile("AAPL")
    assert snap is not None
    assert snap.ticker == "AAPL"
    assert snap.name == "Apple Inc."
    assert snap.exchange == "NASDAQ"
    assert snap.price == Decimal("234.55")
    assert snap.market_cap == Decimal("3500000000000")
    # P/E + EPS are sourced from /ratios-ttm, not /quote.
    assert snap.pe_ratio == Decimal("38.2")
    assert snap.eps == Decimal("6.14")


def test_get_quote_profile_returns_none_on_empty_payload() -> None:
    provider = _build_provider(quote=[], ratios=_ratios_payload())
    assert provider.get_quote_profile("ZZZZ") is None


def test_get_quote_profile_handles_missing_optional_fields() -> None:
    # /quote may omit market cap; /ratios-ttm may omit P/E and EPS.
    provider = _build_provider(
        quote=_quote_payload(marketCap=None),
        ratios=_ratios_payload(
            priceToEarningsRatioTTM=None, netIncomePerShareTTM=None
        ),
    )
    snap = provider.get_quote_profile("AAPL")
    assert snap is not None
    assert snap.pe_ratio is None
    assert snap.eps is None
    assert snap.market_cap is None
    assert snap.price == Decimal("234.55")


def test_get_quote_profile_survives_ratios_failure() -> None:
    # A /ratios-ttm outage must not sink the profile — name / price /
    # market cap still render; P/E and EPS just drop to None.
    def _get(url: str, params: Any = None) -> Any:
        resp = MagicMock()
        if "ratios-ttm" in url:
            resp.raise_for_status.side_effect = RuntimeError("ratios 503")
        else:
            resp.raise_for_status.return_value = None
            resp.json.return_value = _quote_payload()
        return resp

    mock_http = MagicMock()
    mock_http.get.side_effect = _get
    provider = FmpProvider(api_key="fake", http=mock_http)
    snap = provider.get_quote_profile("AAPL")
    assert snap is not None
    assert snap.name == "Apple Inc."
    assert snap.price == Decimal("234.55")
    assert snap.market_cap == Decimal("3500000000000")
    assert snap.pe_ratio is None
    assert snap.eps is None


# ---------------------------------------------------------------------------
# 2. TickerProfileView — endpoint behavior.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOW_PLATFORM_DATA_KEYS=True, FMP_API_KEY="platform-key")
def test_profile_view_upserts_companyprofile_and_caches(monkeypatch) -> None:
    u = User.objects.create_user(email="p1@x.com", password="x" * 12)
    client = APIClient()
    client.force_authenticate(u)

    # Patch the provider factory to return our mock.
    from apps.data import views as data_views
    from apps.data.interfaces import ProfileSnapshot

    snap = ProfileSnapshot(
        ticker="AAPL", name="Apple Inc.", exchange="NASDAQ", sector="",
        price=Decimal("234.55"), market_cap=Decimal("3500000000000"),
        pe_ratio=Decimal("38.2"), eps=Decimal("6.14"),
        shares_outstanding=14920000000, as_of=dt.date(2026, 5, 22),
    )
    mock_fmp = MagicMock()
    mock_fmp.get_quote_profile.return_value = snap
    monkeypatch.setattr(
        "apps.data.providers.factory.get_fmp_provider",
        lambda **kw: mock_fmp,
    )
    # Bypass Redis — pretend cache is always cold/empty.
    monkeypatch.setattr(data_views, "cache_get", lambda key: None)
    set_calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        data_views, "cache_set",
        lambda key, value, ttl_seconds=None: set_calls.append((key, value)),
    )

    res = client.get("/api/tickers/AAPL/profile/")
    assert res.status_code == 200
    body = res.json()
    assert body["ticker"] == "AAPL"
    assert body["name"] == "Apple Inc."
    assert body["market_cap"] == "3500000000000"
    assert body["pe_ratio"] == "38.2"
    assert body["eps"] == "6.14"

    # CompanyProfile got upserted.
    cp = CompanyProfile.objects.get(ticker="AAPL")
    assert cp.name == "Apple Inc."
    assert cp.exchange == "NASDAQ"

    # cache_set was called.
    assert len(set_calls) == 1
    assert set_calls[0][0] == "profile:fmp:AAPL"


@pytest.mark.django_db
def test_profile_view_returns_cached_payload_without_fmp_call(monkeypatch) -> None:
    u = User.objects.create_user(email="p2@x.com", password="x" * 12)
    client = APIClient()
    client.force_authenticate(u)

    from apps.data import views as data_views

    cached_payload = {
        "ticker": "MSFT", "name": "Microsoft Corp",
        "exchange": "NASDAQ", "sector": "",
        "price": "418.20", "market_cap": "3100000000000",
        "pe_ratio": "36.1", "eps": "11.57",
        "shares_outstanding": 7430000000,
        "as_of": "2026-05-22",
    }
    monkeypatch.setattr(data_views, "cache_get", lambda key: cached_payload)
    # Make the factory raise so we'd notice if it got called.
    monkeypatch.setattr(
        "apps.data.providers.factory.get_fmp_provider",
        lambda **kw: (_ for _ in ()).throw(AssertionError("FMP must not be called when cached")),
    )

    res = client.get("/api/tickers/MSFT/profile/")
    assert res.status_code == 200
    assert res.json()["name"] == "Microsoft Corp"


@pytest.mark.django_db
@override_settings(ALLOW_PLATFORM_DATA_KEYS=False, FMP_API_KEY="")
def test_profile_view_degrades_gracefully_without_fmp_key(monkeypatch) -> None:
    """P2n contract: no key → return CompanyProfile fallback + actionable msg."""
    u = User.objects.create_user(email="p3@x.com", password="x" * 12)
    CompanyProfile.objects.create(
        ticker="NVDA", name="NVIDIA Corp", exchange="NASDAQ", sector="Tech",
    )
    client = APIClient()
    client.force_authenticate(u)

    from apps.data import views as data_views
    monkeypatch.setattr(data_views, "cache_get", lambda key: None)

    res = client.get("/api/tickers/NVDA/profile/")
    assert res.status_code == 200  # never errors out
    body = res.json()
    assert body["name"] == "NVIDIA Corp"  # cached identity still served
    assert body["market_cap"] is None  # no live metrics without key
    assert "/settings/models" in body.get("detail", "")


# ---------------------------------------------------------------------------
# 3. TickerProfileBatchView — name-only batch.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_batch_serves_known_companyprofiles_without_fmp_call(monkeypatch) -> None:
    u = User.objects.create_user(email="b1@x.com", password="x" * 12)
    CompanyProfile.objects.create(ticker="AAPL", name="Apple Inc.")
    CompanyProfile.objects.create(ticker="MSFT", name="Microsoft Corp")

    client = APIClient()
    client.force_authenticate(u)

    monkeypatch.setattr(
        "apps.data.providers.factory.get_fmp_provider",
        lambda **kw: (_ for _ in ()).throw(AssertionError("FMP must not be called")),
    )

    res = client.get("/api/tickers/profiles/?symbols=AAPL,MSFT")
    assert res.status_code == 200
    profiles = res.json()["profiles"]
    assert profiles["AAPL"]["name"] == "Apple Inc."
    assert profiles["MSFT"]["name"] == "Microsoft Corp"


@pytest.mark.django_db
def test_batch_caps_fanout_at_50_symbols(monkeypatch) -> None:
    u = User.objects.create_user(email="b2@x.com", password="x" * 12)
    client = APIClient()
    client.force_authenticate(u)

    # No CompanyProfile rows, no FMP key — every symbol is "unknown".
    monkeypatch.setattr(
        "apps.data.providers.factory.get_fmp_provider",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("no key")),
    )

    syms = ",".join(f"SYM{i:03d}" for i in range(100))
    res = client.get(f"/api/tickers/profiles/?symbols={syms}")
    assert res.status_code == 200
    profiles = res.json()["profiles"]
    # Cap at 50, all empty (no key + no cached rows).
    assert len(profiles) == 50
    for entry in profiles.values():
        assert entry["name"] == ""


@pytest.mark.django_db
def test_batch_never_errors_on_unknown_ticker(monkeypatch) -> None:
    """Bad/unknown tickers should return empty entries, not raise."""
    u = User.objects.create_user(email="b3@x.com", password="x" * 12)
    client = APIClient()
    client.force_authenticate(u)

    mock_fmp = MagicMock()
    mock_fmp.get_quote_profile.return_value = None  # unknown ticker
    monkeypatch.setattr(
        "apps.data.providers.factory.get_fmp_provider",
        lambda **kw: mock_fmp,
    )

    res = client.get("/api/tickers/profiles/?symbols=ZZZZ,XXXX")
    assert res.status_code == 200
    profiles = res.json()["profiles"]
    assert profiles["ZZZZ"]["name"] == ""
    assert profiles["XXXX"]["name"] == ""


# ---------------------------------------------------------------------------
# 4. Point-in-time isolation — profile must not leak into backtest paths.
# ---------------------------------------------------------------------------


def test_grep_guard_profile_not_imported_by_pit_paths() -> None:
    """`CompanyProfile` and `get_quote_profile` are *today*-data only. They
    must not appear in backtest engines, agent council nodes, or strategy
    runners (where point-in-time discipline is mandatory).
    """
    import pytest
    repo_root = Path(__file__).resolve().parent.parent.parent
    # See the matching guard in test_p2n_byok_data_providers.py — skip when
    # the working tree isn't a git checkout (e.g. /app-only docker mount).
    check = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True,
    )
    if check.returncode != 0 or check.stdout.strip() != "true":
        pytest.skip("not running inside a git working tree; grep-guard skipped")
    # Search backend code outside the data app's view/url layer.
    proc = subprocess.run(
        [
            "git", "grep", "-nE",
            r"(CompanyProfile|get_quote_profile)",
            "--",
            "backend/**/*.py",
            ":(exclude)backend/apps/data/",
            ":(exclude)backend/tests/",
            ":(exclude)backend/scripts/",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    # No matches outside apps/data is the desired state.
    assert proc.stdout.strip() == "", (
        f"CompanyProfile/get_quote_profile leaked outside apps.data — point-in-time "
        f"safety violated:\n{proc.stdout}"
    )
