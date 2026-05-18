"""L2 cache key stability + free-rerun guarantee.

Locks in: identical (agent_name, agent_version, model, messages) returns a
cache hit on the second call_structured invocation with cost_usd=0.

Also asserts the key is sensitive to: as_of (via message content), model, and
agent_version — so a rerun with any of those changed re-issues the LLM call.
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from apps.backtests import cache as bt_cache
from hedgefund_agents.llm.client import LLMResponse, Message
from hedgefund_agents.llm.structured import call_structured

pytestmark = pytest.mark.django_db


class Tiny(BaseModel):
    answer: str


class FakeClient:
    provider = "fake"

    def __init__(self):
        self.calls = 0

    def complete(self, *, model, messages, max_tokens, temperature, json_mode):
        self.calls += 1
        return LLMResponse(
            text='{"answer":"yes"}', model=model, provider=self.provider,
            prompt_tokens=10, completion_tokens=5, cost_usd=0.0001,
            latency_ms=1, finish_reason="stop",
        )


def _ctx(agent="buf", version="v1"):
    return {"agent_name": agent, "agent_version": version, "enabled": True}


def _msgs(as_of="2025-01-06"):
    return [
        Message("system", "you are a sage"),
        Message("user", f"Ticker: AAPL\nAs-of: {as_of}\nDecide."),
    ]


def test_identical_inputs_hit_cache_on_rerun():
    bt_cache.reset_counters()
    client = FakeClient()
    p1, r1 = call_structured(
        client, model="m1", schema=Tiny, messages=_msgs(), cache_ctx=_ctx(),
    )
    p2, r2 = call_structured(
        client, model="m1", schema=Tiny, messages=_msgs(), cache_ctx=_ctx(),
    )
    assert client.calls == 1, "second call should be served from L2 cache"
    assert p2.answer == p1.answer
    assert r2.cost_usd == 0.0
    assert r2.finish_reason == "cache_hit"
    assert bt_cache.counter("hits") == 1
    assert bt_cache.counter("writes") == 1


@pytest.mark.parametrize("changed", ["model", "version", "as_of"])
def test_cache_misses_when_key_inputs_change(changed):
    bt_cache.reset_counters()
    client = FakeClient()
    call_structured(
        client, model="m1", schema=Tiny, messages=_msgs("2025-01-06"),
        cache_ctx=_ctx(version="v1"),
    )
    kwargs = dict(model="m1", schema=Tiny, messages=_msgs("2025-01-06"),
                  cache_ctx=_ctx(version="v1"))
    if changed == "model":
        kwargs["model"] = "m2"
    elif changed == "version":
        kwargs["cache_ctx"] = _ctx(version="v2")
    elif changed == "as_of":
        kwargs["messages"] = _msgs("2025-02-06")
    call_structured(client, **kwargs)
    assert client.calls == 2, f"changing {changed} must bust the cache"
