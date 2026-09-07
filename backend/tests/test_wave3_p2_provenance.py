"""WAVE-3 P2 item 4 — the data-provenance endpoints.

``GET /api/data/provenance/`` answers "where did this number come from and how
old is it"; ``POST /api/data/provenance/refresh/`` enqueues the existing
bar / dividend / filings refresh for a bounded set of tickers.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.data.models import (
    CorporateAction,
    DailyBar,
    FilingRecord,
    MacroSeries,
    MacroSnapshot,
    NewsItem,
    RegimeModel,
    RegimeSnapshot,
)

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def client_and_user():
    user = User.objects.create_user(email="prov@x.com", password="x" * 12)
    client = APIClient()
    client.force_authenticate(user)
    return client, user


def _seed_aapl():
    DailyBar.objects.create(
        ticker="AAPL", date=dt.date(2026, 9, 2), open=1, high=1, low=1,
        close=Decimal("100"), adjusted_close=Decimal("99.4"), volume=1, source="fmp",
    )
    DailyBar.objects.create(
        ticker="AAPL", date=dt.date(2026, 9, 4), open=1, high=1, low=1,
        close=Decimal("101"), adjusted_close=Decimal("101"), volume=1, source="fmp",
    )
    CorporateAction.objects.create(
        ticker="AAPL", as_of_date=dt.date(2026, 8, 11),
        kind=CorporateAction.CASH_DIVIDEND, amount=Decimal("0.25"), source="fmp",
    )
    FilingRecord.objects.create(
        ticker="AAPL", form_type="10-Q", filed_at=dt.date(2026, 8, 1),
        period_end=dt.date(2026, 6, 30), accession="0000320193-26-000042",
        url="https://sec/x", text_excerpt="body",
    )
    NewsItem.objects.create(
        ticker="AAPL", published_at=timezone.now(), headline="h", source="Reuters",
        provider="tiingo", url="https://n/1", dedup_key="k1",
    )
    NewsItem.objects.create(
        ticker="AAPL", published_at=timezone.now(), headline="h2", source="FMP",
        provider="fmp", url="https://n/2", dedup_key="k2",
    )
    rm = RegimeModel.objects.create(
        ticker="AAPL", as_of_date=dt.date(2026, 9, 5), model_type="labelled_markov",
        config_hash="abc",
        transition_matrix=[[0.9, 0.05, 0.05], [0.05, 0.9, 0.05], [0.05, 0.05, 0.9]],
        state_labels={}, stationary_distribution={}, fit_observations=1,
        observations_available=1, training_start_date=dt.date(2026, 9, 1),
        training_end_date=dt.date(2026, 9, 4),
    )
    RegimeSnapshot.objects.create(
        ticker="AAPL", as_of_date=dt.date(2026, 9, 5), model_type="labelled_markov",
        config_hash="abc", source_model=rm, last_price_date=dt.date(2026, 9, 4),
        current_state="bull", current_return=0.01, current_state_persistence=0.8,
        bull_persistence=0.8, sideways_persistence=0.5, bear_persistence=0.4,
        bull_prob_1d=0.6, sideways_prob_1d=0.3, bear_prob_1d=0.1,
        bull_prob_5d=0.5, sideways_prob_5d=0.3, bear_prob_5d=0.2,
        bull_minus_bear_1d=0.5, stale=False,
    )


def test_provenance_requires_authentication():
    assert APIClient().get("/api/data/provenance/").status_code in (401, 403)


def test_provenance_per_ticker_block(client_and_user):
    client, _user = client_and_user
    _seed_aapl()
    resp = client.get("/api/data/provenance/?tickers=AAPL,MSFT")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert set(body["tickers"]) == {"AAPL", "MSFT"}

    aapl = body["tickers"]["AAPL"]
    assert aapl["bars"] == {
        "last_date": "2026-09-04", "source": "fmp", "count": 2,
        "adjusted_differs_from_close": True,
    }
    assert aapl["dividends"] == {"last_ex_date": "2026-08-11", "count": 1}
    assert aapl["filings"] == {"count": 1, "newest_filed_at": "2026-08-01"}
    assert [n["provider"] for n in aapl["news"]] == ["fmp", "tiingo"]
    assert all(n["count"] == 1 for n in aapl["news"])
    assert aapl["regime"] == {
        "as_of_date": "2026-09-05", "last_price_date": "2026-09-04",
        "stale": False, "model_type": "labelled_markov",
    }

    # A ticker with nothing on file is present and empty, never missing.
    msft = body["tickers"]["MSFT"]
    assert msft["bars"]["last_date"] is None
    assert msft["regime"] is None
    assert msft["news"] == []


def test_provenance_global_block(client_and_user):
    client, _user = client_and_user
    MacroSeries.objects.create(
        series_id="CPIAUCSL", date=dt.date(2026, 8, 1),
        vintage_date=dt.date(2026, 9, 1), value=Decimal("321.5"),
    )
    MacroSnapshot.objects.create(
        as_of_date=dt.date(2026, 9, 6), growth_quadrant="expansion",
        inflation_regime="moderate", yield_curve_state="normal",
        policy_stance="neutral", narrative="n", classifier_version=2,
    )
    resp = client.get("/api/data/provenance/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tickers"] == {}
    macro = body["global"]["macro"]
    assert macro["series"] == [{
        "series_id": "CPIAUCSL",
        "newest_observation_date": "2026-08-01",
        "newest_vintage_date": "2026-09-01",
    }]
    assert macro["classifier_version"] == 2
    assert macro["snapshot_as_of"] == "2026-09-06"
    # Provider key status is reused verbatim from /diagnostics/providers/.
    providers = body["global"]["providers"]
    assert set(providers) >= {"fmp", "tiingo", "fred", "edgar", "openrouter"}
    assert providers["fmp"]["key"] in ("configured", "missing")
    assert "user_byok" in providers["fmp"]
    assert set(body["global"]["provider_last_success"]) == set(providers)


@pytest.mark.parametrize(
    "qs",
    ["tickers=" + ",".join(f"T{i:03d}" for i in range(51)), "tickers=not a ticker!"],
)
def test_provenance_rejects_malformed_input(client_and_user, qs):
    client, _user = client_and_user
    resp = client.get(f"/api/data/provenance/?{qs}")
    assert resp.status_code == 400
    assert "detail" in resp.json()


def test_provenance_accepts_exactly_the_cap(client_and_user):
    client, _user = client_and_user
    qs = ",".join(f"T{i:03d}" for i in range(50))
    resp = client.get(f"/api/data/provenance/?tickers={qs}")
    assert resp.status_code == 200
    assert len(resp.json()["tickers"]) == 50


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------


def test_refresh_enqueues_one_task_per_ticker(client_and_user, monkeypatch):
    client, user = client_and_user
    seen: list[tuple] = []

    class _Res:
        id = "task-123"

    monkeypatch.setattr(
        "apps.data.tasks.refresh_ticker_data.delay",
        lambda *a, **k: (seen.append((a, k)), _Res())[1],
    )
    resp = client.post(
        "/api/data/provenance/refresh/", {"tickers": ["aapl", "MSFT", "aapl"]},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    # Deduplicated + upper-cased.
    assert body["queued"] == 2
    assert body["task_ids"] == ["task-123", "task-123"]
    assert [a[0] for a, _k in seen] == ["AAPL", "MSFT"]
    assert all(a[1] == user.id for a, _k in seen)


def test_refresh_rejects_empty_and_oversized_input(client_and_user):
    client, _user = client_and_user
    assert client.post(
        "/api/data/provenance/refresh/", {"tickers": []}, format="json"
    ).status_code == 400
    assert client.post(
        "/api/data/provenance/refresh/",
        {"tickers": [f"T{i:03d}" for i in range(51)]}, format="json",
    ).status_code == 400
    assert client.post(
        "/api/data/provenance/refresh/", {"tickers": "AA PL"}, format="json"
    ).status_code == 400


def test_refresh_requires_authentication():
    resp = APIClient().post(
        "/api/data/provenance/refresh/", {"tickers": ["AAPL"]}, format="json"
    )
    assert resp.status_code in (401, 403)


def test_refresh_task_delegates_to_the_existing_helpers(monkeypatch):
    """The task adds no refresh logic of its own — it calls
    ``freshness.refresh_universe_bars`` and the EDGAR filings provider."""
    from apps.data import tasks

    calls: dict[str, object] = {}

    monkeypatch.setattr(
        "apps.data.providers.factory.get_fmp_provider", lambda user=None: "FMP"
    )
    monkeypatch.setattr(
        "apps.data.freshness.refresh_universe_bars",
        lambda tickers, as_of, provider, **kw: calls.update(
            bars=(list(tickers), provider)
        ),
    )

    class _Edgar:
        def get_recent_filings(self, ticker, *, as_of, form_types, limit=4):
            calls["filings"] = (ticker, tuple(form_types))
            return []

    monkeypatch.setattr(
        "apps.data.providers.factory.get_edgar_provider", lambda: _Edgar()
    )
    out = tasks.refresh_ticker_data("aapl", None)
    assert out == {"ticker": "AAPL", "bars": "ok", "filings": "ok"}
    assert calls["bars"] == (["AAPL"], "FMP")
    assert calls["filings"][0] == "AAPL"


def test_refresh_task_reports_a_missing_key_instead_of_raising(monkeypatch):
    from apps.data import tasks

    def _no_key(user=None):
        raise RuntimeError("No FMP key configured.")

    monkeypatch.setattr("apps.data.providers.factory.get_fmp_provider", _no_key)

    class _Edgar:
        def get_recent_filings(self, ticker, *, as_of, form_types, limit=4):
            raise RuntimeError("EDGAR down")

    monkeypatch.setattr(
        "apps.data.providers.factory.get_edgar_provider", lambda: _Edgar()
    )
    out = tasks.refresh_ticker_data("AAPL", None)
    assert out["bars"].startswith("no_key")
    assert out["filings"] == "error: RuntimeError"
