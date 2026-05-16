"""Shared helper: persist an LLMResponse as an LLMCall row."""
from __future__ import annotations

from decimal import Decimal

from .llm.client import LLMResponse
from .models import LLMCall


def record_llm_call(
    *, run_id: int | None, agent_name: str, resp: LLMResponse
) -> LLMCall:
    return LLMCall.objects.create(
        run_id=run_id,
        agent_name=agent_name,
        provider=resp.provider,
        model=resp.model,
        prompt_tokens=resp.prompt_tokens,
        cached_tokens=resp.cached_tokens,
        completion_tokens=resp.completion_tokens,
        cost_usd=Decimal(f"{resp.cost_usd:.6f}"),
        latency_ms=resp.latency_ms,
    )
