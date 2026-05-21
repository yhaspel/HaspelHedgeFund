"""Pair-council quality tests.

Two layers:
  * Fast, deterministic mocked-LLM tests that wire a stub `call_structured`
    into the council and verify the aggregator + thresholds behave as the
    plan requires (skip on trap-shaped votes; enter on benign votes).
  * One live-LLM smoke test gated on @pytest.mark.live_llm — runs each
    trap fixture through `debate_pair` with the frugal preset so a real
    Qwen-via-OpenRouter call decides. Skipped by default; opt in with
    `RUN_LIVE_LLM_TESTS=1 uv run pytest -m live_llm tests/test_pairs_council.py`.
"""
from __future__ import annotations

import os

import pytest

from hedgefund_agents.pairs_council import (
    PairPersonaVote,
    _aggregate,
    debate_pair,
)
from tests.fixtures.trap_pairs import FAIR_PAIRS, TRAP_PAIRS

# ---------- Aggregator unit tests ---------------------------------------

def test_aggregator_skip_when_majority_skip_votes():
    votes = [
        PairPersonaVote(persona="burry", action="skip", confidence=0.85,
                        thesis="catalyst-driven divergence"),
        PairPersonaVote(persona="druckenmiller", action="skip", confidence=0.70,
                        thesis="macro headwind on leg_a"),
        PairPersonaVote(persona="lynch", action="enter", confidence=0.40,
                        thesis="might be noise"),
    ]
    out = _aggregate(votes)
    assert out.action == "skip"
    assert out.aggregate_confidence > 0.5
    assert out.skip_count == 2
    assert out.enter_count == 1


def test_aggregator_enter_when_majority_enter_votes():
    votes = [
        PairPersonaVote(persona="burry", action="enter", confidence=0.80,
                        thesis="no catalyst"),
        PairPersonaVote(persona="druckenmiller", action="enter", confidence=0.65,
                        thesis="regime-neutral divergence"),
        PairPersonaVote(persona="lynch", action="skip", confidence=0.55,
                        thesis="business shift"),
    ]
    out = _aggregate(votes)
    assert out.action == "enter"
    assert out.enter_count == 2


def test_aggregator_no_votes_returns_safe_skip():
    out = _aggregate([])
    assert out.action == "skip"
    assert out.aggregate_confidence == 0.0


# ---------- Mocked-LLM trap-fixture tests ------------------------------

def _mock_call_structured_factory(canned_action: str, confidence: float = 0.80):
    """Returns a function with `call_structured`'s signature that always
    parses to a PairPersonaVote with the given action.
    """
    from hedgefund_agents.llm.client import LLMResponse

    def _fake(client, *, model, schema, messages, max_tokens=2048, temperature=0.2,
             cache_ctx=None):
        # Discover the persona from the system message voice prefix.
        sys_msg = messages[0].content if messages else ""
        persona = "burry"
        for name in ("buffett", "munger", "graham", "lynch", "wood",
                     "druckenmiller", "burry", "damodaran"):
            if f"channelling {name.capitalize()}" in sys_msg:
                persona = name
                break
        vote = schema(
            persona=persona, action=canned_action, confidence=confidence,
            thesis=f"[{persona} stub] {canned_action}",
            structural_break_risks=[],
        )
        resp = LLMResponse(text=vote.model_dump_json(), model=model,
                           provider="mock", cost_usd=0.0)
        return vote, resp

    return _fake


@pytest.mark.parametrize("trap", TRAP_PAIRS, ids=lambda t: t.name)
def test_council_skips_traps_when_personas_vote_skip(monkeypatch, trap):
    """Wire a stub LLM that returns 'skip' for every persona; verify the
    council's aggregate verdict is skip. This is the aggregator-half of
    the trap-veto contract; the LLM-half is exercised in the live test."""
    monkeypatch.setattr(
        "hedgefund_agents.pairs_council.call_structured",
        _mock_call_structured_factory("skip", confidence=0.80),
    )
    decision = debate_pair(
        leg_a=trap.leg_a, leg_b=trap.leg_b, sector=trap.sector,
        as_of=trap.as_of,
        z_current=trap.z_current, correlation=trap.correlation,
        p_value=trap.p_value,
        personas=["burry", "druckenmiller", "lynch"],
        news_by_ticker=trap.news_by_ticker,
    )
    assert decision.action == "skip", f"trap '{trap.name}' should skip"
    assert decision.skip_count == 3


def test_council_enters_fair_pair_when_personas_vote_enter(monkeypatch):
    fair = FAIR_PAIRS[0]
    monkeypatch.setattr(
        "hedgefund_agents.pairs_council.call_structured",
        _mock_call_structured_factory("enter", confidence=0.75),
    )
    decision = debate_pair(
        leg_a=fair.leg_a, leg_b=fair.leg_b, sector=fair.sector,
        as_of=fair.as_of,
        z_current=fair.z_current, correlation=fair.correlation,
        p_value=fair.p_value,
        personas=["burry", "druckenmiller", "lynch"],
        news_by_ticker=fair.news_by_ticker,
    )
    assert decision.action == "enter"
    assert decision.enter_count == 3


# ---------- Live-LLM smoke test (opt-in) -------------------------------

LIVE_LLM_ON = os.environ.get("RUN_LIVE_LLM_TESTS") == "1"
FRUGAL_MODEL = "openrouter:qwen/qwen3.6-27b"


@pytest.mark.live_llm
@pytest.mark.skipif(not LIVE_LLM_ON, reason="set RUN_LIVE_LLM_TESTS=1 to run")
@pytest.mark.parametrize("trap", TRAP_PAIRS, ids=lambda t: t.name)
def test_live_council_handles_trap_fixture(trap):
    """End-to-end against the real LLM on the frugal preset. We only assert
    that the council *runs* (votes are collected from all three personas
    and the aggregator returns a valid decision); we record but don't
    strictly assert the action so the test isn't flaky on a single LLM
    call. The combined record over the parametrized run is what reviewers
    inspect to gauge council quality."""
    overrides = {p: FRUGAL_MODEL for p in ("burry", "druckenmiller", "lynch")}
    decision = debate_pair(
        leg_a=trap.leg_a, leg_b=trap.leg_b, sector=trap.sector,
        as_of=trap.as_of,
        z_current=trap.z_current, correlation=trap.correlation,
        p_value=trap.p_value,
        personas=["burry", "druckenmiller", "lynch"],
        news_by_ticker=trap.news_by_ticker,
        model_overrides=overrides,
    )
    assert decision.action in ("enter", "skip")
    assert decision.enter_count + decision.skip_count == len(decision.votes)
    # At least one persona must have voted (LLM didn't fully fail).
    assert len(decision.votes) >= 1, f"trap {trap.name}: no votes returned"
