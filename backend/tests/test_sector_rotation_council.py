"""P2h refinement 2 — sector_rotation council-v2 (screener-led / council-as-veto).

These tests cover the PM-level behaviour in isolation: under flavor=sector_rotation,
a screener-picked ETF should default to buy unless a persona votes bearish at
>= bearish_veto_threshold, or RM vetoes.

Council graph wiring (build_sector_council_graph) is exercised via a smoke
test that asserts the node set is the slim variant (no fundamentals/valuation).
"""
from __future__ import annotations

from hedgefund_agents.graphs.council import build_sector_council_graph
from hedgefund_agents.portfolio.portfolio_manager import (
    aggregate,
    build_sector_veto_entry,
)


def _persona(signal: str, confidence: int) -> dict:
    return {"signal": signal, "confidence": confidence, "thesis": "", "risks": []}


def _aggregate_sector(
    persona_outputs, risk=None, bearish_veto_threshold=0.70
):
    return aggregate(
        ticker="XLK",
        persona_outputs=persona_outputs,
        risk=risk or {},
        valuation={},
        pm_config={
            "flavor": "sector_rotation",
            "bearish_veto_threshold": bearish_veto_threshold,
            "buy_threshold": 0.10,
            "sell_threshold": -0.10,
        },
        trailing_returns=None,
        portfolio_value=100_000.0,
    )


def test_all_neutral_yields_buy():
    """The screener pre-selected XLK; if every persona abstains (neutral),
    council does not block → action="buy". This is the central fix."""
    out = _aggregate_sector({
        "druckenmiller": _persona("neutral", 50),
        "damodaran": _persona("neutral", 55),
        "burry": _persona("neutral", 60),
    })
    assert out.action == "buy"


def test_low_confidence_bearish_does_not_block():
    """A bearish vote below the threshold is not a veto — neutral-equivalent."""
    out = _aggregate_sector({
        "druckenmiller": _persona("neutral", 50),
        "damodaran": _persona("bearish", 65),  # below 70
        "burry": _persona("neutral", 60),
    })
    assert out.action == "buy"


def test_high_confidence_bearish_vetoes():
    """A single persona at confidence >= threshold blocks the trade."""
    out = _aggregate_sector({
        "druckenmiller": _persona("bullish", 80),
        "damodaran": _persona("neutral", 55),
        "burry": _persona("bearish", 75),  # blocks
    })
    assert out.action == "hold"


def test_rm_veto_blocks_even_if_personas_bullish():
    out = _aggregate_sector(
        {
            "druckenmiller": _persona("bullish", 80),
            "damodaran": _persona("bullish", 75),
            "burry": _persona("bullish", 70),
        },
        risk={"veto": True, "veto_reason": "concentration"},
    )
    assert out.action == "hold"


def test_threshold_is_tunable():
    persona_outs = {
        "druckenmiller": _persona("neutral", 50),
        "damodaran": _persona("bearish", 65),
        "burry": _persona("neutral", 60),
    }
    # At 0.70 (default), 65 doesn't trigger.
    assert _aggregate_sector(persona_outs).action == "buy"
    # At 0.60, the same vote vetoes.
    assert _aggregate_sector(
        persona_outs, bearish_veto_threshold=0.60
    ).action == "hold"


def test_build_sector_veto_entry_shape():
    entry = build_sector_veto_entry(
        "XLE",
        {
            "druckenmiller": _persona("bullish", 80),
            "burry": _persona("bearish", 85),
        },
        risk={},
        threshold=0.70,
    )
    assert entry["ticker"] == "XLE"
    assert entry["decision"] == "veto"
    assert entry["rm_veto"] is False
    assert entry["threshold_pct"] == 70
    assert entry["reasons"] == [
        {"persona": "burry", "signal": "bearish", "confidence": 85}
    ]


def test_build_sector_veto_entry_buy_path():
    entry = build_sector_veto_entry(
        "XLF",
        {"druckenmiller": _persona("neutral", 50)},
        risk={},
        threshold=0.70,
    )
    assert entry["decision"] == "buy"
    assert entry["reasons"] == []
    assert entry["rm_veto"] is False


def test_sector_council_graph_is_slim():
    """No fundamentals/valuation/sentiment nodes — they don't apply to ETFs."""
    g = build_sector_council_graph(personas=["druckenmiller", "burry"])
    node_names = set(g.get_graph().nodes.keys())
    # Slim analytical set.
    assert "technicals" in node_names
    assert "macro" in node_names
    assert "news_digest" in node_names
    # Excluded because they have no signal on ETFs.
    assert "fundamentals" not in node_names
    assert "valuation" not in node_names
    assert "sentiment" not in node_names
    # Selected personas only.
    assert "druckenmiller" in node_names
    assert "burry" in node_names
    assert "buffett" not in node_names


def test_sector_council_default_personas():
    """When no personas list is passed, the default macro trio is wired."""
    g = build_sector_council_graph()
    node_names = set(g.get_graph().nodes.keys())
    assert {"druckenmiller", "damodaran", "burry"}.issubset(node_names)
