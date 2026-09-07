"""LLMClient Protocol + shared dataclasses.

Every adapter (Anthropic direct, OpenRouter, Ollama-in-P2+) implements
the same Protocol so the agent layer is provider-agnostic. Switching from
Sonnet to Qwen3 is *only* a model-id change in the Run config.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    finish_reason: str = ""
    raw: dict = field(default_factory=dict)
    # Billed-but-rejected attempts that preceded this one (structured.py
    # retries up to 3× on empty/invalid JSON). The provider charges for every
    # one of them, so `_persist.record_llm_call` writes an LLMCall row per
    # entry — otherwise Run.total_cost_usd, /costs/summary/ and the mid-run
    # budget guard all under-count the real spend.
    prior_attempts: list[LLMResponse] = field(default_factory=list)

    @property
    def attempts(self) -> int:
        return 1 + len(self.prior_attempts)


@runtime_checkable
class LLMClient(Protocol):
    provider: str

    def complete(
        self,
        *,
        model: str,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> LLMResponse: ...
