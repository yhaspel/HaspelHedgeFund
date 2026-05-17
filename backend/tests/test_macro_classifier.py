"""Macro regime classifier — deterministic, no LLM. Tests cover each axis."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from apps.data.providers.fred import MacroObservation
from hedgefund_agents.macro.macro_agent import classify_regime


def _obs(series_id: str, value: float | None) -> MacroObservation:
    return MacroObservation(
        series_id=series_id,
        date=dt.date(2024, 12, 31),
        vintage_date=dt.date(2024, 12, 31),
        value=Decimal(str(value)) if value is not None else None,
    )


def test_recession_when_unemployment_high_and_indpro_weak():
    out = classify_regime({
        "UNRATE": _obs("UNRATE", 6.0),
        "INDPRO": _obs("INDPRO", 95.0),
    })
    assert out["growth_quadrant"] == "recession"


def test_expansion_when_unemployment_low_and_indpro_strong():
    out = classify_regime({
        "UNRATE": _obs("UNRATE", 3.7),
        "INDPRO": _obs("INDPRO", 105.0),
    })
    assert out["growth_quadrant"] == "expansion"


def test_yield_curve_inverted_when_spread_negative():
    out = classify_regime({"T10Y2Y": _obs("T10Y2Y", -0.5)})
    assert out["yield_curve_state"] == "inverted"


def test_yield_curve_inverted_via_dgs10_dgs2_fallback():
    out = classify_regime({
        "DGS10": _obs("DGS10", 4.0),
        "DGS2": _obs("DGS2", 4.5),
    })
    assert out["yield_curve_state"] == "inverted"


def test_policy_tightening_when_fedfunds_high():
    out = classify_regime({"FEDFUNDS": _obs("FEDFUNDS", 5.25)})
    assert out["policy_stance"] == "tightening"


def test_policy_easing_when_fedfunds_low():
    out = classify_regime({"FEDFUNDS": _obs("FEDFUNDS", 1.0)})
    assert out["policy_stance"] == "easing"


def test_missing_inputs_fall_through_to_defaults():
    out = classify_regime({})
    assert out["growth_quadrant"] == "expansion"
    assert out["inflation_regime"] == "moderate"
    assert out["yield_curve_state"] == "normal"
    assert out["policy_stance"] == "neutral"
