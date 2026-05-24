"""call_structured retry budget must not escalate past MAX_ATTEMPT_TOKENS.

Pre-fix: empty-content retry doubled to 8K → 32K → 131K, triggering OpenRouter
HTTP 400 "max context 131072" on the third attempt and burning ~40K wasted
output tokens chasing a reasoning model that's just churning thinking tokens.
"""
from __future__ import annotations

from pydantic import BaseModel

from hedgefund_agents.llm.client import LLMResponse, Message
from hedgefund_agents.llm.structured import MAX_ATTEMPT_TOKENS, call_structured


class _Tiny(BaseModel):
    value: str


class _AlwaysEmpty:
    """Fake LLM client that always returns empty content (mimics a reasoning
    model that burns its whole budget on hidden thinking tokens)."""
    provider = "fake"

    def __init__(self):
        self.max_tokens_seen: list[int] = []

    def complete(self, *, model, messages, max_tokens, temperature, json_mode):
        self.max_tokens_seen.append(max_tokens)
        return LLMResponse(
            text="", model=model, provider=self.provider,
            prompt_tokens=10, completion_tokens=max_tokens,
            cost_usd=0.0, finish_reason="length",
        )


def test_retry_budget_capped_at_max_attempt_tokens():
    client = _AlwaysEmpty()
    try:
        call_structured(
            client, model="fake/m", schema=_Tiny,
            messages=[Message("user", "hi")],
            max_tokens=2048,
        )
    except ValueError:
        pass  # expected — 3 attempts of empty content surface a ValueError

    # Three attempts. Budgets: 2048 (initial) → min(8192, CAP) → min(32768, CAP).
    assert len(client.max_tokens_seen) == 3
    assert client.max_tokens_seen[0] == 2048
    assert all(t <= MAX_ATTEMPT_TOKENS for t in client.max_tokens_seen)
    # Critical: the cap holds even when the natural 4× escalation would
    # blow past the context ceiling.
    assert client.max_tokens_seen[-1] == MAX_ATTEMPT_TOKENS
