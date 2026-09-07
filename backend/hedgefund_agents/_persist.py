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
    # A structured call can burn up to 3 billed requests before one parses
    # (see llm/structured.py). Persist EVERY attempt — recording only the last
    # one under-counted Run.total_cost_usd, /costs/summary/ and the mid-run
    # budget guard by up to 3×.
    attempts = [*getattr(resp, "prior_attempts", []), resp]
    # cost_usd < 0 is the "unknown price" sentinel from pricing.estimate_cost.
    # Preserve it on the LLMCall row so the call is visibly unpriced, but do
    # not aggregate negatives into Run/Backtest totals.
    call = LLMCall.objects.bulk_create([
        LLMCall(
            run_id=run_id,
            backtest_id=backtest_id,
            portfolio_target_id=portfolio_target_id,
            agent_name=agent_name,
            provider=a.provider,
            model=a.model,
            prompt_tokens=a.prompt_tokens,
            cached_tokens=a.cached_tokens,
            completion_tokens=a.completion_tokens,
            cost_usd=Decimal(f"{a.cost_usd:.6f}"),
            latency_ms=a.latency_ms,
        )
        for a in attempts
    ])[-1]
    cost = sum(
        (Decimal(f"{a.cost_usd:.6f}") for a in attempts if a.cost_usd > 0),
        Decimal("0"),
    )
    if cost > 0:
        # Import locally to avoid a circular import at module load.
        if run_id is not None:
            from apps.runs.models import Run

            Run.objects.filter(pk=run_id).update(total_cost_usd=F("total_cost_usd") + cost)
            _enforce_run_budget(run_id)
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


def _enforce_run_budget(run_id: int) -> None:
    """Mid-run LLM-spend backstop (P5-SH WS1.2).

    Re-read the run's running total and cap; if an effective cap is set and the
    summed cost has crossed it, raise ``BudgetExceeded`` so the run aborts
    between agent nodes. This mirrors ``backtests/engine.py``'s ``_ingest``
    check, and — like ``ModelUnavailable`` — the node self-heal wrapper re-raises
    it rather than degrading to a null signal (see graphs/_node_fallback.py).

    A NULL ``Run.max_budget_usd`` falls back to
    ``settings.RUN_DEFAULT_MAX_BUDGET_USD`` (itself ``None`` = off by default),
    so the guard is a no-op unless the operator opts in. Backtests never reach
    here (they persist under ``backtest_id``, not ``run_id``).
    """
    from django.conf import settings

    from apps.backtests.exceptions import BudgetExceeded
    from apps.runs.models import Run

    row = (
        Run.objects.filter(pk=run_id)
        .values_list("total_cost_usd", "max_budget_usd")
        .first()
    )
    if row is None:
        return
    spent, cap = row
    if cap is None:
        cap = getattr(settings, "RUN_DEFAULT_MAX_BUDGET_USD", None)
    if cap is None:
        return
    cap = Decimal(str(cap))
    if spent is not None and spent >= cap:
        n_calls = LLMCall.objects.filter(run_id=run_id).count()
        raise BudgetExceeded(float(spent), float(cap), n_calls, n_calls)
