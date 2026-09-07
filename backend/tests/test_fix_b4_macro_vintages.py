"""WP B4 — macro regime classifier v2 regression vintages.

``classify_regime`` used to read *index levels* (CPIAUCSL >= 320 -> "high"
inflation; an ``INDPRO <= 100`` recession gate against the 2017 base year).
Both are non-stationary, so inflation read "high" forever after ~2025 and
"recession" became structurally unreachable. v2 classifies on rates of
change: CPI/INDPRO YoY, a 3-month UNRATE change (Sahm-style) and a 6-month
fed-funds change; the 2s10s spread is unchanged (a spread is already a rate).

Each case below is an approximate FRED vintage: the value as of the date,
plus the same series at the classifier's lookback.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.data.providers.fred import MacroObservation
from hedgefund_agents.macro.macro_agent import (
    CLASSIFIER_VERSION,
    DERIVED_LOOKBACK_MONTHS,
    classify_regime,
)


def _o(d: dt.date, **values: float) -> dict[str, MacroObservation | None]:
    return {
        k: MacroObservation(series_id=k, date=d, vintage_date=d, value=Decimal(str(v)))
        for k, v in values.items()
    }


# (label, as_of, now-values, lagged values, expected regime)
VINTAGES = [
    (
        "2008-07 — GFC onset",
        dt.date(2008, 7, 1),
        dict(CPIAUCSL=219.0, INDPRO=102.0, UNRATE=5.8, FEDFUNDS=2.0, T10Y2Y=1.35),
        dict(CPIAUCSL=207.6, INDPRO=104.6, UNRATE=5.0, FEDFUNDS=4.25),
        dict(
            growth_quadrant="recession",     # UNRATE +0.8pp in 3m (Sahm)
            inflation_regime="high",         # CPI +5.5% YoY
            yield_curve_state="normal",      # 2s10s +1.35
            policy_stance="easing",          # fed funds 4.25 -> 2.00
        ),
    ),
    (
        "2019-08 — late expansion, curve inverts",
        dt.date(2019, 8, 1),
        dict(CPIAUCSL=256.0, INDPRO=109.6, UNRATE=3.7, FEDFUNDS=2.13, T10Y2Y=-0.04),
        dict(CPIAUCSL=251.6, INDPRO=109.0, UNRATE=3.6, FEDFUNDS=2.40),
        dict(
            growth_quadrant="expansion",     # INDPRO +0.6% YoY, UNRATE +0.1pp
            inflation_regime="low",          # CPI +1.7% YoY
            yield_curve_state="inverted",
            policy_stance="neutral",         # -0.27 over 6m: below the 0.50 bar
        ),
    ),
    (
        "2022-06 — inflation shock, hiking cycle",
        dt.date(2022, 6, 1),
        dict(CPIAUCSL=295.3, INDPRO=104.0, UNRATE=3.6, FEDFUNDS=1.68, T10Y2Y=0.05),
        dict(CPIAUCSL=271.7, INDPRO=99.6, UNRATE=3.6, FEDFUNDS=0.08),
        dict(
            growth_quadrant="expansion",     # INDPRO +4.4% YoY, UNRATE flat
            inflation_regime="high",         # CPI +8.7% YoY
            yield_curve_state="flat",
            policy_stance="tightening",      # +1.60 over 6m
        ),
    ),
    (
        "2026-09 — current: high CPI LEVEL, tame inflation RATE",
        dt.date(2026, 9, 1),
        dict(CPIAUCSL=326.0, INDPRO=104.5, UNRATE=4.2, FEDFUNDS=3.60, T10Y2Y=0.60),
        dict(CPIAUCSL=317.7, INDPRO=103.0, UNRATE=4.2, FEDFUNDS=3.85),
        dict(
            growth_quadrant="expansion",     # INDPRO +1.5% YoY, UNRATE flat
            inflation_regime="moderate",     # CPI +2.6% YoY (v1 said "high")
            yield_curve_state="normal",
            policy_stance="neutral",
        ),
    ),
]


@pytest.mark.parametrize(
    "label,as_of,now,lagged,expected", VINTAGES, ids=[v[0] for v in VINTAGES]
)
def test_regime_vintage(label, as_of, now, lagged, expected):
    out = classify_regime(_o(as_of, **now), _o(as_of, **lagged))
    assert out == expected, label


def test_index_level_alone_no_longer_decides_anything():
    """The same CPI/INDPRO LEVELS with different rates of change must produce
    different regimes — that is the whole point of v2."""
    hot = classify_regime(
        _o(dt.date(2026, 9, 1), CPIAUCSL=326.0, INDPRO=104.0, UNRATE=4.2),
        _o(dt.date(2025, 9, 1), CPIAUCSL=300.0, INDPRO=108.0, UNRATE=3.6),
    )
    calm = classify_regime(
        _o(dt.date(2026, 9, 1), CPIAUCSL=326.0, INDPRO=104.0, UNRATE=4.2),
        _o(dt.date(2025, 9, 1), CPIAUCSL=320.0, INDPRO=102.5, UNRATE=4.2),
    )
    assert hot["inflation_regime"] == "high" and calm["inflation_regime"] == "low"
    assert hot["growth_quadrant"] == "recession"      # INDPRO -3.7% YoY
    assert calm["growth_quadrant"] == "expansion"


def test_classifier_version_and_lookbacks_are_declared():
    assert CLASSIFIER_VERSION == 2
    assert DERIVED_LOOKBACK_MONTHS == {
        "CPIAUCSL": 12, "INDPRO": 12, "UNRATE": 3, "FEDFUNDS": 6
    }


@pytest.mark.django_db
def test_snapshot_stamps_the_classifier_version_and_old_rows_default_to_v1():
    from apps.data.models import MacroSnapshot

    old = MacroSnapshot.objects.create(
        as_of_date=dt.date(2026, 1, 5), growth_quadrant="expansion",
        inflation_regime="high", yield_curve_state="normal", policy_stance="neutral",
        narrative="written by v1",
    )
    old.refresh_from_db()
    assert old.classifier_version == 1  # readable, and identifiable as v1


@pytest.mark.django_db
def test_compute_snapshot_derives_the_lagged_readings_from_the_local_cache(monkeypatch):
    """The lagged observations come from ``MacroSeries`` (already backfilled by
    ``get_latest_value``), so v2 costs no extra ALFRED calls."""
    from apps.data.models import MacroSeries, MacroSnapshot
    from hedgefund_agents.macro import macro_agent

    as_of = dt.date(2026, 9, 1)
    rows = [
        ("CPIAUCSL", dt.date(2026, 8, 1), "326.0"),
        ("CPIAUCSL", dt.date(2025, 8, 1), "317.7"),
        ("INDPRO", dt.date(2026, 8, 1), "104.5"),
        ("INDPRO", dt.date(2025, 8, 1), "103.0"),
        ("UNRATE", dt.date(2026, 8, 1), "4.2"),
        ("UNRATE", dt.date(2026, 5, 1), "4.2"),
        ("FEDFUNDS", dt.date(2026, 8, 1), "3.60"),
        ("FEDFUNDS", dt.date(2026, 2, 1), "3.85"),
        ("T10Y2Y", dt.date(2026, 8, 31), "0.60"),
    ]
    MacroSeries.objects.bulk_create(
        MacroSeries(series_id=s, date=d, vintage_date=as_of, value=Decimal(v))
        for s, d, v in rows
    )

    class _Fred:
        calls = 0

        def get_latest_value(self, series_id, *, as_of):
            _Fred.calls += 1
            row = (
                MacroSeries.objects.filter(series_id=series_id, date__lte=as_of)
                .order_by("-date")
                .first()
            )
            return macro_agent._to_obs_or_none(row)

    monkeypatch.setattr(
        macro_agent, "_llm_narrative", lambda **kw: ("n", {})
    )
    snap = macro_agent.compute_snapshot(as_of, provider=_Fred())
    assert isinstance(snap, MacroSnapshot)
    assert snap.classifier_version == 2
    assert snap.inflation_regime == "moderate"   # +2.6% YoY, not "high" on level
    assert snap.growth_quadrant == "expansion"
    assert snap.yield_curve_state == "normal"
    # One ALFRED-shaped call per configured series and nothing more: the lagged
    # readings are DB reads.
    assert _Fred.calls == len(macro_agent.MACRO_SERIES)
