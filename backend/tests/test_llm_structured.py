"""Unit tests for the structured-output helper with a fake client."""
from __future__ import annotations

from pydantic import BaseModel

from hedgefund_agents.llm.client import LLMResponse, Message
from hedgefund_agents.llm.structured import call_structured


class Toy(BaseModel):
    n: int
    s: str


class _Fake:
    provider = "fake"

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls = 0

    def complete(self, *, model, messages, max_tokens=2048, temperature=0.2, json_mode=False):
        self.calls += 1
        return LLMResponse(
            text=self._replies.pop(0), model=model, provider=self.provider,
            prompt_tokens=10, completion_tokens=5, cost_usd=0.0, latency_ms=1,
        )


def test_structured_succeeds_first_try() -> None:
    fake = _Fake(['{"n": 1, "s": "hi"}'])
    parsed, _ = call_structured(
        fake, model="x", schema=Toy, messages=[Message("user", "go")]
    )
    assert parsed == Toy(n=1, s="hi")
    assert fake.calls == 1


def test_structured_retries_once_on_invalid_json() -> None:
    fake = _Fake(["not json at all", '{"n": 2, "s": "ok"}'])
    parsed, _ = call_structured(
        fake, model="x", schema=Toy, messages=[Message("user", "go")]
    )
    assert parsed.n == 2
    assert fake.calls == 2


def test_structured_extracts_from_fenced_block() -> None:
    fake = _Fake(['```json\n{"n": 3, "s": "fenced"}\n```'])
    parsed, _ = call_structured(
        fake, model="x", schema=Toy, messages=[Message("user", "go")]
    )
    assert parsed.s == "fenced"
