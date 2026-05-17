"""Portfolio Manager aggregation + dissent + veto handling."""
from __future__ import annotations

from hedgefund_agents.personas import ALL_PERSONAS
from hedgefund_agents.portfolio.portfolio_manager import (
    aggregate_personas,
    find_dissent,
    map_to_action,
    run_portfolio_manager,
)


def _persona(signal: str, conf: int = 80, thesis: str = "ok") -> dict:
    return {"signal": signal, "confidence": conf, "thesis": thesis}


def test_aggregate_all_bullish_is_buy() -> None:
    outs = {name: _persona("bullish", 90) for name in ALL_PERSONAS[:5]}
    score, conf = aggregate_personas(outs)
    assert score > 0.5
    assert map_to_action(score) == "buy"
    assert 80 <= conf <= 100


def test_aggregate_all_bearish_is_sell() -> None:
    outs = {name: _persona("bearish", 90) for name in ALL_PERSONAS[:5]}
    score, _ = aggregate_personas(outs)
    assert score < -0.5
    assert map_to_action(score) == "sell"


def test_split_is_hold() -> None:
    outs = {
        "buffett": _persona("bullish", 80),
        "munger": _persona("bearish", 80),
        "graham": _persona("neutral", 50),
    }
    score, _ = aggregate_personas(outs)
    assert map_to_action(score) == "hold"


def test_dissent_captured() -> None:
    outs = {
        "buffett": _persona("bullish", 90, "moat"),
        "munger": _persona("bullish", 80, "ok"),
        "graham": _persona("bullish", 70, "value"),
        "wood": _persona("bullish", 80, "innov"),
        "burry": _persona("bearish", 75, "fragile"),
    }
    dissent = find_dissent(outs, "buy")
    assert [d.name for d in dissent] == ["burry"]
    assert dissent[0].signal == "bearish"


def test_pm_respects_risk_veto() -> None:
    state = {
        "ticker": "AAPL",
        "buffett": _persona("bullish", 90),
        "munger": _persona("bullish", 90),
        "graham": _persona("bullish", 90),
        "risk": {
            "veto": True,
            "max_position_pct_for_this_trade": 0.0,
            "hard_caps_applied": ["max_portfolio_drawdown_pct"],
        },
        "valuation": {"current_price": 100.0},
    }
    d = run_portfolio_manager(state)["decision"]
    assert d["action"] == "hold"
    assert d["target_quantity"] == 0.0
    assert d["target_weight_pct"] == 0.0


def test_pm_sizes_buy_within_cap() -> None:
    state = {
        "ticker": "AAPL",
        "buffett": _persona("bullish", 90),
        "munger": _persona("bullish", 90),
        "graham": _persona("bullish", 90),
        "wood": _persona("bullish", 90),
        "druckenmiller": _persona("bullish", 90),
        "risk": {"veto": False, "max_position_pct_for_this_trade": 0.10, "hard_caps_applied": []},
        "valuation": {"current_price": 200.0},
    }
    d = run_portfolio_manager(state)["decision"]
    assert d["action"] == "buy"
    assert d["target_weight_pct"] <= 10.0001
    assert d["target_quantity"] > 0
