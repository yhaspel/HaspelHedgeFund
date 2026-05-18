"""PM config plumbing: weights override, thresholds, vol-targeted sizing."""
from __future__ import annotations

from hedgefund_agents.portfolio.portfolio_manager import (
    aggregate,
    aggregate_personas,
    compute_target_weight,
    realized_vol_annual,
)


def _p(signal, conf=80):
    return {"signal": signal, "confidence": conf, "thesis": ""}


def test_weights_override_changes_aggregation():
    outs = {
        "buffett": _p("bullish", 90),
        "burry": _p("bearish", 90),
    }
    # Equal -> roughly zero. Skew weights to buffett -> bullish.
    score_eq, _ = aggregate_personas(outs, weights={"buffett": 0.5, "burry": 0.5})
    score_skew, _ = aggregate_personas(outs, weights={"buffett": 0.9, "burry": 0.1})
    assert score_skew > score_eq


def test_threshold_changes_action():
    outs = {"buffett": _p("bullish", 50), "munger": _p("bullish", 40)}
    # Lower threshold -> buy
    res_low = aggregate(
        ticker="AAA", persona_outputs=outs, risk={}, valuation={"current_price": 100.0},
        pm_config={"buy_threshold": 0.05, "sell_threshold": -0.05, "min_confidence": 0.0},
    )
    # Higher threshold -> hold
    res_high = aggregate(
        ticker="AAA", persona_outputs=outs, risk={}, valuation={"current_price": 100.0},
        pm_config={"buy_threshold": 0.95, "sell_threshold": -0.95, "min_confidence": 0.0},
    )
    assert res_low.action == "buy"
    assert res_high.action == "hold"


def test_vol_targeted_sizing_inverse_to_vol():
    # Constant up-trend -> low vol -> bigger position
    rets_low_vol = [0.001] * 60
    # Noisy -> high vol -> smaller position
    rets_high_vol = [0.05, -0.05] * 30
    vlow = realized_vol_annual(rets_low_vol)
    vhigh = realized_vol_annual(rets_high_vol)
    assert vhigh > vlow

    cfg = {"vol_target_annual": 0.10, "max_weight": 1.0, "vol_floor": 0.001}
    w_low = compute_target_weight(
        signed=0.5, action="buy", confidence=80, cap=0.0, cfg=cfg, trailing_returns=rets_low_vol,
    )
    w_high = compute_target_weight(
        signed=0.5, action="buy", confidence=80, cap=0.0, cfg=cfg, trailing_returns=rets_high_vol,
    )
    assert w_low > w_high
    assert 0 < w_low <= 1.0
    assert 0 < w_high <= 1.0


def test_min_confidence_gate():
    outs = {"buffett": _p("bullish", 30), "munger": _p("bullish", 25)}
    res = aggregate(
        ticker="AAA", persona_outputs=outs, risk={}, valuation={"current_price": 100.0},
        pm_config={"buy_threshold": 0.01, "min_confidence": 0.9},
    )
    assert res.action == "hold"  # confidence below gate
