"""L2 LLMResponseCache: keying, hit/miss telemetry, version invalidation."""

from __future__ import annotations

import pytest

from apps.backtests.cache import (
    _bump,
    build_key,
    counter,
    lookup,
    make_cache_ctx,
    reset_counters,
    store,
)

pytestmark = pytest.mark.django_db


class _Msg:
    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


def test_make_cache_ctx_disabled_when_flag_false():
    assert make_cache_ctx({}, "buffett") is None
    assert make_cache_ctx({"use_llm_cache": False}, "buffett") is None


def test_make_cache_ctx_emits_version_from_state():
    ctx = make_cache_ctx(
        {"use_llm_cache": True, "agent_versions": {"buffett": "v3"}},
        "buffett",
    )
    assert ctx == {"agent_name": "buffett", "agent_version": "v3", "enabled": True}


def test_build_key_is_deterministic_and_version_sensitive():
    msgs = [_Msg("system", "sys"), _Msg("user", "ticker=AAPL")]
    kwargs = dict(agent_name="buffett", model="haiku", messages=msgs, schema_name="X")
    k1 = build_key(agent_version="v1", **kwargs)
    k2 = build_key(agent_version="v1", **kwargs)
    assert k1 == k2
    k3 = build_key(agent_version="v2", **kwargs)
    assert k1 != k3


def test_store_and_lookup_roundtrip():
    reset_counters()
    msgs = [_Msg("user", "hi")]
    key = build_key(agent_name="a", agent_version="v1", model="m", messages=msgs)
    assert lookup(key) is None
    store(
        cache_key=key,
        agent_name="a",
        agent_version="v1",
        response_json={"signal": "bullish"},
        tokens_in=10,
        tokens_out=20,
        cost_usd=0.01,
    )
    got = lookup(key)
    assert got == {"signal": "bullish"}


def test_counters_track_bumps():
    reset_counters()
    _bump("hits")
    _bump("hits")
    _bump("misses")
    assert counter("hits") == 2
    assert counter("misses") == 1


def test_agent_versions_reaches_graph_nodes():
    """Regression: agent_versions set in the initial state MUST reach graph
    nodes so make_cache_ctx can stamp the L2 cache key with the real version.

    The bug: agent_versions was not declared on AgentState, so
    StateGraph(AgentState) silently dropped it from the input. Every node then
    saw an empty dict, make_cache_ctx resolved a blank version, and every
    LLMResponseCache row was stored version-less — meaning a version bump never
    invalidated the cache (a stale-output footgun the cache docstring promises
    against). This reproduces the propagation end-to-end on a real compiled
    StateGraph so the field can't be dropped again without failing.
    """
    from langgraph.graph import END, StateGraph

    from apps.backtests.cache import make_cache_ctx
    from hedgefund_agents.base import AgentState

    seen: dict = {}

    def node(state: AgentState) -> dict:
        seen["ctx"] = make_cache_ctx(state, "buffett")
        return {}

    graph = StateGraph(AgentState)
    graph.add_node("only", node)
    graph.set_entry_point("only")
    graph.add_edge("only", END)
    compiled = graph.compile()

    compiled.invoke(
        {
            "ticker": "AAPL",
            "use_llm_cache": True,
            "agent_versions": {"buffett": "v2:abc123"},
        }
    )

    # The node must have received agent_versions through the compiled graph and
    # resolved the real version — not the blank fallback.
    assert seen["ctx"] == {
        "agent_name": "buffett",
        "agent_version": "v2:abc123",
        "enabled": True,
    }
