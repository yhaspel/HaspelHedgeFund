"""Trivial Portfolio Manager — echoes Buffett's signal verbatim. Real PM
arrives in Phase 2a (Agent Council)."""
from __future__ import annotations

from ..base import AgentState
from ..outputs import PMDecision

_SIGNAL_TO_ACTION = {"bullish": "buy", "bearish": "sell", "neutral": "hold"}


def run_trivial_pm(state: AgentState) -> AgentState:
    buffett = state.get("buffett") or {}
    signal = buffett.get("signal", "neutral")
    decision = PMDecision(
        ticker=state["ticker"],
        action=_SIGNAL_TO_ACTION.get(signal, "hold"),
        confidence=int(buffett.get("confidence", 0)),
        rationale=(buffett.get("thesis") or "").strip()[:1000],
    )
    state["decision"] = decision.model_dump()
    return state
