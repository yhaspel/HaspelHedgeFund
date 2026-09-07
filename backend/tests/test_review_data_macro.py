"""Adversarial review (reviewer: data) — macro regime classifier + snapshot endpoint.

Proof tests for:
  * F-CPI: ``classify_regime`` classifies *inflation* from the CPI index LEVEL
    (CPIAUCSL, 1982-84=100), not from an inflation rate. Any date after ~2025
    is "high" forever; 2008 (5.6% YoY) is "low".
  * F-INDPRO: ``growth_quadrant`` uses the INDPRO index LEVEL (2017=100) as a
    recession gate, so "recession" becomes unreachable once INDPRO > 100.
  * F-MACRO-ASOF: ``MacroSnapshotView`` synchronously (re)builds a snapshot —
    FRED fetch + LLM narrative on the platform key — for ANY ``as_of`` older
    than the earliest stored snapshot, per request, with no guard.
  * F-MACRO-STALE: the view's ``stale`` flag ignores snapshot age.
  * F-ASOF-500: a malformed ``as_of`` query param is an unhandled ValueError.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.data.models import MacroSnapshot
from apps.data.providers.fred import MacroObservation
from hedgefund_agents.macro.macro_agent import classify_regime

pytestmark = pytest.mark.django_db

User = get_user_model()


def _obs(**values: float) -> dict[str, MacroObservation | None]:
    d = dt.date(2024, 1, 1)
    return {
        k: MacroObservation(series_id=k, date=d, vintage_date=d, value=Decimal(str(v)))
        for k, v in values.items()
    }


def _prior(**values: float) -> dict[str, MacroObservation | None]:
    d = dt.date(2023, 1, 1)
    return {
        k: MacroObservation(series_id=k, date=d, vintage_date=d, value=Decimal(str(v)))
        for k, v in values.items()
    }


# ---------------------------------------------------------------------------
# F-CPI — inflation regime is now a function of the YoY inflation RATE
# ---------------------------------------------------------------------------


def test_inflation_regime_is_the_yoy_rate_not_the_index_level():
    """FIXED: CPIAUCSL is an index (1982-84=100), so its level says nothing
    about inflation. Classification now runs on the YoY % change."""
    # 2026: CPIAUCSL 326 vs 317.6 a year ago -> +2.6% YoY -> moderate.
    assert (
        classify_regime(_obs(CPIAUCSL=326.0), _prior(CPIAUCSL=317.6))["inflation_regime"]
        == "moderate"
    )
    # Mid-2008: 218 vs 206.4 -> +5.6% YoY -> high (v1 said "low").
    assert (
        classify_regime(_obs(CPIAUCSL=218.0), _prior(CPIAUCSL=206.4))["inflation_regime"]
        == "high"
    )
    # 1980: 82 vs 72.2 -> +13.6% YoY -> high (v1 said "low").
    assert (
        classify_regime(_obs(CPIAUCSL=82.0), _prior(CPIAUCSL=72.2))["inflation_regime"]
        == "high"
    )
    # No longer monotone in the level: an ever-higher index with tame YoY is
    # never forced to "high".
    for level in (321.0, 350.0, 400.0, 800.0):
        out = classify_regime(_obs(CPIAUCSL=level), _prior(CPIAUCSL=level / 1.018))
        assert out["inflation_regime"] == "low", level
    # And with no year-ago print the level alone yields the neutral default.
    assert classify_regime(_obs(CPIAUCSL=326.0))["inflation_regime"] == "moderate"


# ---------------------------------------------------------------------------
# F-INDPRO — recession is reachable again (YoY + Sahm-style UNRATE change)
# ---------------------------------------------------------------------------


def test_recession_is_reachable_regardless_of_the_indpro_index_level():
    """FIXED: the recession gate no longer compares INDPRO (2017=100) to its
    own base year."""
    # INDPRO down 8% YoY (113 -> 104) with unemployment up 1.4pp in 3 months.
    out = classify_regime(_obs(UNRATE=7.0, INDPRO=104.0), _prior(UNRATE=5.6, INDPRO=113.0))
    assert out["growth_quadrant"] == "recession"
    # 2009-style: INDPRO still above the 2017 base, unemployment spiking.
    out = classify_regime(_obs(UNRATE=10.0, INDPRO=101.0), _prior(UNRATE=9.0, INDPRO=110.0))
    assert out["growth_quadrant"] == "recession"
    # Level-only fallback (no history) uses unemployment alone, not INDPRO.
    assert classify_regime(_obs(UNRATE=7.0, INDPRO=104.0))["growth_quadrant"] == "recession"
    # Pre-2017 history: INDPRO ~85 and growing is an EXPANSION, not a
    # structural non-expansion because the index sits below its base year.
    out = classify_regime(_obs(UNRATE=4.0, INDPRO=85.0), _prior(UNRATE=4.1, INDPRO=82.0))
    assert out["growth_quadrant"] == "expansion"
    assert classify_regime(_obs(UNRATE=4.0, INDPRO=85.0))["growth_quadrant"] == "expansion"


# ---------------------------------------------------------------------------
# F-MACRO-ASOF — any authenticated user can trigger a synchronous rebuild
# (FRED + LLM narrative) for arbitrary historical dates, one per request.
# ---------------------------------------------------------------------------


def test_macro_snapshot_view_never_rebuilds_for_an_old_as_of(monkeypatch):
    """FIXED: an arbitrary historical ``as_of`` is served from the newest
    stored row and marked stale — no inline FRED sweep + LLM narrative."""
    user = User.objects.create_user(email="l@example.com", password="x" * 12)
    # A single, current snapshot exists (what the beat job maintains).
    MacroSnapshot.objects.create(
        as_of_date=dt.date(2026, 9, 1), growth_quadrant="expansion",
        inflation_regime="high", yield_curve_state="normal", policy_stance="neutral",
        narrative="n",
    )
    calls: list[str] = []

    def fake_compute(as_of, *a, **kw):
        calls.append(as_of.isoformat())
        return MacroSnapshot.objects.create(
            as_of_date=as_of, growth_quadrant="x", inflation_regime="x",
            yield_curve_state="x", policy_stance="x", narrative="llm-narrative",
        )

    monkeypatch.setattr(
        "hedgefund_agents.macro.macro_agent.compute_snapshot", fake_compute
    )
    client = APIClient()
    client.force_authenticate(user)
    for d in ("1999-01-06", "1999-01-05", "1999-01-04"):
        resp = client.get("/api/macro/snapshot/", {"as_of": d})
        # Nothing is stored at or before 1999 -> honest 503, no rebuild.
        assert resp.status_code == 503, resp.content
    assert calls == []
    assert MacroSnapshot.objects.count() == 1


def test_macro_snapshot_view_enqueues_only_for_recent_as_of(monkeypatch):
    """A recent ``as_of`` with a usable stored row refreshes via ``.delay()``
    and serves the stored row immediately."""
    from apps.data import tasks as data_tasks

    user = User.objects.create_user(email="l2@example.com", password="x" * 12)
    MacroSnapshot.objects.create(
        as_of_date=dt.date.today() - dt.timedelta(days=2), growth_quadrant="expansion",
        inflation_regime="moderate", yield_curve_state="normal",
        policy_stance="neutral", narrative="stored",
    )
    inline: list[str] = []
    delayed: list[str] = []
    monkeypatch.setattr(
        data_tasks.prewarm_macro_snapshot, "delay", lambda iso: delayed.append(iso)
    )
    monkeypatch.setattr(
        "hedgefund_agents.macro.macro_agent.compute_snapshot",
        lambda as_of, *a, **kw: inline.append(as_of.isoformat()),
    )
    client = APIClient()
    client.force_authenticate(user)
    resp = client.get("/api/macro/snapshot/", {"as_of": dt.date.today().isoformat()})
    assert resp.status_code == 200, resp.content
    assert resp.json()["narrative"] == "stored"
    assert inline == []                                   # nothing built in-request
    assert delayed == [dt.date.today().isoformat()]       # refreshed out of band


def test_macro_snapshot_view_stale_flag_counts_age():
    """FIXED: an 8-month-old snapshot is served with ``stale: true``."""
    user = User.objects.create_user(email="u2@example.com", password="x" * 12)
    MacroSnapshot.objects.create(
        as_of_date=dt.date(2026, 1, 5), growth_quadrant="expansion",
        inflation_regime="high", yield_curve_state="normal", policy_stance="neutral",
        narrative="n",
    )
    client = APIClient()
    client.force_authenticate(user)
    resp = client.get("/api/macro/snapshot/", {"as_of": "2026-09-07"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["as_of_date"] == "2026-01-05"
    assert body["stale"] is True
    assert body["snapshot_age_days"] > 200
    assert body["classifier_version"] == 1  # written before the v2 classifier


# ---------------------------------------------------------------------------
# F-ASOF-500 — malformed as_of is an unhandled ValueError -> HTTP 500
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/macro/snapshot/",
        "/api/macro/regime/SPY/",
        "/api/macro/regime/batch/",
        "/api/tickers/SPY/sparkline/",
        "/api/tickers/SPY/news/",
    ],
)
def test_malformed_as_of_is_a_400(path):
    """FIXED: one shared parse helper raises a DRF ValidationError -> 400."""
    user = User.objects.create_user(email="u3@example.com", password="x" * 12)
    client = APIClient()
    client.force_authenticate(user)
    client.raise_request_exception = False
    resp = client.get(path, {"as_of": "2026-13-45"})
    assert resp.status_code == 400, (path, resp.status_code)
    assert "2026-13-45" in str(resp.json())


# ---------------------------------------------------------------------------
# F-FRED-VINTAGE-BLOAT — every as_of fetch re-persists the entire series
# ---------------------------------------------------------------------------


def test_fred_cache_rewrites_full_history_per_as_of():
    """ALFRED clips each observation's ``realtime_start`` to the request's
    realtime period (FRED docs example: realtime_start=realtime_end=2013-08-14
    returns every observation, 1929 onwards, stamped realtime_start=2013-08-14).
    ``FredProvider`` stores that as ``vintage_date``, which is part of the
    unique key — so a fetch for a new as_of duplicates the whole history."""
    from unittest.mock import MagicMock

    from apps.data.models import MacroSeries
    from apps.data.providers.fred import FredProvider

    n_obs = 16_000  # DGS10: daily since 1962

    def _get(url, params=None):
        as_of = params["realtime_start"]
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "observations": [
                {"realtime_start": as_of, "realtime_end": as_of,
                 "date": (dt.date(1962, 1, 2) + dt.timedelta(days=i)).isoformat(),
                 "value": "4.0"}
                for i in range(n_obs)
            ]
        }
        return resp

    http = MagicMock()
    http.get.side_effect = _get
    prov = FredProvider(api_key="k", http=http)
    prov.get_latest_value("DGS10", as_of=dt.date(2026, 9, 1))
    assert MacroSeries.objects.count() == n_obs
    # 8 days later the 7-day freshness check expires -> full re-fetch, and
    # every row is new because vintage_date differs.
    prov.get_latest_value("DGS10", as_of=dt.date(2026, 9, 9))
    assert MacroSeries.objects.count() == 2 * n_obs
    assert http.get.call_count == 2
