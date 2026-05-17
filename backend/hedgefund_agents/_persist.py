"""Shared helper: persist an LLMResponse as an LLMCall row and bump the
parent Run.total_cost_usd atomically.

Cost is updated incrementally per call (rather than only at run completion)
so cancelled/failed runs still surface the correct partial cost.
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import F

from .llm.client import LLMResponse
from .models import LLMCall


def record_llm_call(
    *, run_id: int | None, agent_name: str, resp: LLMResponse
) -> LLMCall:
    cost = Decimal(f"{resp.cost_usd:.6f}")
    call = LLMCall.objects.create(
        run_id=run_id,
        agent_name=agent_name,
        provider=resp.provider,
        model=resp.model,
        prompt_tokens=resp.prompt_tokens,
        cached_tokens=resp.cached_tokens,
        completion_tokens=resp.completion_tokens,
        cost_usd=cost,
        latency_ms=resp.latency_ms,
    )
    if run_id is not None and cost:
        # Import locally to avoid a circular import at module load.
        from apps.runs.models import Run

        Run.objects.filter(pk=run_id).update(total_cost_usd=F("total_cost_usd") + cost)
    return call
