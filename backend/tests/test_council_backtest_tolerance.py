"""Backtest-tolerant council: per-agent LLM failures must NOT abort
the ticker-day. The wrapper in hedgefund_agents/graphs/_node_fallback.py
turns each agent into "always returns a state delta" — failed agents emit a
null-signal dict, and downstream PM/CIO aggregate whatever's available.

Without this, a low per-agent failure rate p compounds across N agents to a
ticker-day failure rate of 1 - (1 - p)^N — which is why bt15/bt16
aborted_partial despite the engine and the prime path being structurally
fine.
"""
from __future__ import annotations

import pytest

from hedgefund_agents.graphs._node_fallback import (
    _fallback_for,
    wrap_backtest_tolerant,
)


def _raising_node(_state):
    raise RuntimeError("synthetic LLM failure")


def test_live_run_degrades_when_self_heal_on(settings):
    """Self-healing (RUN_SELF_HEAL, default True): a residual unrecoverable
    per-agent failure in a LIVE run degrades just that agent to a null signal
    (marked _degraded for visibility) so the council proceeds and the run
    completes 'done' instead of FAILED."""
    settings.RUN_SELF_HEAL = True
    wrapped = wrap_backtest_tolerant(_raising_node, "fundamentals")
    out = wrapped({"ticker": "AAPL"})  # live: no backtest_id
    assert out["fundamentals"]["_degraded"] is True
    assert out["fundamentals"]["notes"].startswith("fallback")


def test_live_run_propagates_when_self_heal_off(settings):
    """With RUN_SELF_HEAL off, live runs keep the original fail-loud behavior so
    an operator who wants exceptions surfaced can opt out of degradation."""
    settings.RUN_SELF_HEAL = False
    wrapped = wrap_backtest_tolerant(_raising_node, "fundamentals")
    with pytest.raises(RuntimeError, match="synthetic LLM failure"):
        wrapped({"ticker": "AAPL"})


def test_backtest_returns_null_signal_for_known_agent():
    """In backtest context (state.backtest_id is set), failure → null-signal
    dict for the state key. The graph proceeds; PM aggregates whatever's
    available."""
    wrapped = wrap_backtest_tolerant(_raising_node, "fundamentals")
    out = wrapped({"ticker": "AAPL", "backtest_id": 42})
    assert "fundamentals" in out
    fb = out["fundamentals"]
    assert fb["quality_score"] == 0
    assert fb["notes"].startswith("fallback")


def test_backtest_stamps_ticker_into_fallback_when_appropriate():
    """For agents whose schema includes `ticker` (news_digest, decision, cio),
    the wrapper stamps the live ticker into the null-signal dict so downstream
    readers don't see an empty string."""
    for key in ("news_digest", "decision", "cio"):
        wrapped = wrap_backtest_tolerant(_raising_node, key)
        out = wrapped({"ticker": "MSFT", "backtest_id": 7})
        assert out[key]["ticker"] == "MSFT"


def test_persona_fallback_works_for_all_persona_names():
    """All 8 persona names route to the shared PersonaOutput fallback."""
    for persona in ("buffett", "munger", "graham", "wood",
                    "druckenmiller", "burry", "damodaran", "lynch"):
        wrapped = wrap_backtest_tolerant(_raising_node, persona)
        out = wrapped({"ticker": "AAPL", "backtest_id": 1})
        assert out[persona]["signal"] == "neutral"
        assert out[persona]["confidence"] == 0
        assert out[persona]["thesis"].startswith("fallback")


def test_unknown_state_key_re_raises():
    """If a node's state key isn't registered in _FALLBACKS, the wrapper
    re-raises rather than silently swallowing — protects against typos and
    new agents that haven't had a fallback registered yet."""
    wrapped = wrap_backtest_tolerant(_raising_node, "made_up_agent_xyz")
    with pytest.raises(RuntimeError, match="synthetic LLM failure"):
        wrapped({"ticker": "AAPL", "backtest_id": 1})


def test_backtest_re_raises_fatal_model_errors():
    """ModelUnavailable (and its RateLimited subclass) must NOT be masked as a
    null signal in backtest context — they recur on every ticker-day, so the
    wrapper re-raises and lets prime_agent_cache abort the whole run once with
    the actionable upstream message."""
    from apps.backtests.exceptions import ModelUnavailable, RateLimited

    def _mu_node(_state):
        raise ModelUnavailable(model="dead/model:free", status_code=402, body="Out of credits")

    with pytest.raises(ModelUnavailable):
        wrap_backtest_tolerant(_mu_node, "fundamentals")({"ticker": "AAPL", "backtest_id": 1})

    def _rl_node(_state):
        raise RateLimited(model="x:free", body="pool saturated")

    with pytest.raises(RateLimited):
        wrap_backtest_tolerant(_rl_node, "fundamentals")({"ticker": "AAPL", "backtest_id": 1})


def test_successful_node_passes_through_untouched():
    """Wrapper is transparent when the node succeeds — same state delta out."""
    def ok_node(state):
        return {"fundamentals": {"quality_score": 73, "notes": "ok"}}
    wrapped = wrap_backtest_tolerant(ok_node, "fundamentals")
    out = wrapped({"ticker": "AAPL", "backtest_id": 1})
    assert out["fundamentals"]["quality_score"] == 73
    assert out["fundamentals"]["notes"] == "ok"


def test_fallback_dicts_are_distinct_copies():
    """Successive invocations return DIFFERENT dict objects so mutating one
    fallback doesn't pollute the next. (Without the dict() copy in
    _fallback_for, all callers would share the same module-level template.)"""
    fb1 = _fallback_for("fundamentals")
    fb2 = _fallback_for("fundamentals")
    assert fb1 is not fb2
    fb1["notes"] = "mutated"
    assert _fallback_for("fundamentals")["notes"].startswith("fallback")


def test_council_math_failure_rate_under_per_agent_tolerance():
    """The structural argument for this fix, expressed as a calculation: a
    10-agent council with 5% per-agent failure rate. OLD semantics (all-or-
    nothing): ~40% ticker-day failure rate. NEW semantics (per-agent
    fallback): per-agent failures stay at 5%, ticker-day always completes."""
    p = 0.05
    n = 10
    old_ticker_day_failure_rate = 1 - (1 - p) ** n
    assert 0.39 < old_ticker_day_failure_rate < 0.41
    # Under the new wrapper, *every* ticker-day completes regardless of how
    # many agents fail (because graph.invoke never raises in backtest
    # context). The "loss" surfaces as per-key null signals in the cache map,
    # not as a missing (ticker, day) entry.
