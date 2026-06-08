"""Backtest.personas must actually select the voting council.

Prime always runs the full persona roster, and aggregate_personas re-includes
any persona not in the candidate weight vector at a default quality weight of
1.0 -- so before this fix `Backtest.personas` was a no-op for the vote.
persona_outputs_from_cache restricts the council in run_segment. Pure-python.
"""
from __future__ import annotations

from apps.backtests.engine import persona_outputs_from_cache

_CACHED = {
    "druckenmiller": {"signal": "bullish", "confidence": 80},
    "buffett": {"signal": "bearish", "confidence": 70},
    "graham": {"signal": "bearish", "confidence": 75},
    # non-persona analytical/risk/valuation keys that must always be dropped:
    "technicals": {"signal": "bullish"},
    "macro": {"signal": "neutral"},
    "risk": {"veto": False},
    "valuation": {"current_price": 100.0},
    "fundamentals": {"quality_score": 50},
    "sentiment": {"signal": "neutral"},
    "news_digest": {"summary": "x"},
}


def test_drops_non_persona_keys_when_unrestricted() -> None:
    out = persona_outputs_from_cache(_CACHED, allowed=None)
    assert set(out) == {"druckenmiller", "buffett", "graham"}


def test_restricts_to_selected_personas() -> None:
    out = persona_outputs_from_cache(_CACHED, allowed=["druckenmiller"])
    assert set(out) == {"druckenmiller"}
    assert out["druckenmiller"]["signal"] == "bullish"


def test_empty_personas_is_noop() -> None:
    # Falsy `allowed` (None / []) keeps the full roster (legacy behavior).
    assert set(persona_outputs_from_cache(_CACHED, allowed=[])) == {
        "druckenmiller", "buffett", "graham",
    }


def test_allowed_persona_absent_from_cache_is_empty() -> None:
    # Requesting a persona that was not primed yields an empty council (the
    # aggregator handles the no-vote case) rather than erroring.
    assert persona_outputs_from_cache(_CACHED, allowed=["nonexistent"]) == {}


def test_restriction_removes_bearish_drag_in_aggregate() -> None:
    # The actual bug: with a single-persona weight vector, the non-selected
    # bearish personas still drag the aggregate via the quality fallback.
    from hedgefund_agents.portfolio.portfolio_manager import aggregate_personas

    weights = {"druckenmiller": 1.0}
    full = persona_outputs_from_cache(_CACHED, allowed=None)
    only = persona_outputs_from_cache(_CACHED, allowed=["druckenmiller"])

    signed_full, _ = aggregate_personas(full, weights=weights)
    signed_only, _ = aggregate_personas(only, weights=weights)

    assert signed_full < 0      # buffett/graham still drag it bearish (the leak)
    assert signed_only > 0      # restricted council is purely bullish
    assert signed_only > signed_full
