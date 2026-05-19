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
    *, run_id: int | None, agent_name: str, resp: LLMResponse,
    backtest_id: int | None = None,
    portfolio_target_id: int | None = None,
) -> LLMCall:
    # cost_usd < 0 is the "unknown price" sentinel from pricing.estimate_cost.
    # Preserve it on the LLMCall row so the call is visibly unpriced, but do
    # not aggregate negatives into Run/Backtest totals.
    cost = Decimal(f"{resp.cost_usd:.6f}")
    call = LLMCall.objects.create(
        run_id=run_id,
        backtest_id=backtest_id,
        portfolio_target_id=portfolio_target_id,
        agent_name=agent_name,
        provider=resp.provider,
        model=resp.model,
        prompt_tokens=resp.prompt_tokens,
        cached_tokens=resp.cached_tokens,
        completion_tokens=resp.completion_tokens,
        cost_usd=cost,
        latency_ms=resp.latency_ms,
    )
    if cost > 0:
        # Import locally to avoid a circular import at module load.
        if run_id is not None:
            from apps.runs.models import Run

            Run.objects.filter(pk=run_id).update(total_cost_usd=F("total_cost_usd") + cost)
        if backtest_id is not None:
            from apps.backtests.models import Backtest

            Backtest.objects.filter(pk=backtest_id).update(
                total_cost_usd=F("total_cost_usd") + cost
            )
        if portfolio_target_id is not None:
            from apps.portfolios.models import PortfolioTarget

            PortfolioTarget.objects.filter(pk=portfolio_target_id).update(
                total_cost_usd=F("total_cost_usd") + cost
            )
    return call
