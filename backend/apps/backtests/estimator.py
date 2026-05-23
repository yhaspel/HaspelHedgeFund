"""Pre-flight cost + duration estimator for backtests.

`estimate_cost(...)` returns a dict the UI uses to warn before submission.
Counts the LLM invocations the run will issue, multiplies by historical
average per-call cost+latency (per agent_name + resolved model), and
compares to the configured `max_budget_usd`. Falls back to coarse
constants when no history exists.

NOT a guarantee — token counts vary per ticker/day. The hard kill-switch
in prime_agent_cache is the actual safety net; this is the warning layer.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from decimal import Decimal
from statistics import mean
from typing import Any

from apps.data.models import DailyBar
from hedgefund_agents.llm.pricing import PRICING, lookup_catalog_price
from hedgefund_agents.models import LLMCall
from hedgefund_agents.personas import ALL_PERSONAS
from hedgefund_agents.registry import DEFAULT_MODELS

from .engine import rebalance_dates_for

ANALYTICAL_AGENTS = ["fundamentals", "technicals", "valuation", "sentiment"]
PIPELINE_AGENTS = ["macro", "news_digest", "risk_manager", "portfolio_manager"]
# CIO is skipped in backtests (engine.py sets disable_cio=True).

# Fallback per-call cost ($) if no historical LLMCall rows exist for an agent.
# Tuned to Haiku 4.5 averages observed in P2c smoke runs.
FALLBACK_COST_PER_CALL = {
    "fundamentals": 0.004, "technicals": 0.003, "valuation": 0.004,
    "sentiment": 0.003, "macro": 0.002, "news_digest": 0.003,
    "risk_manager": 0.003, "portfolio_manager": 0.002,
}
FALLBACK_PERSONA_CALL = 0.006  # personas synthesize more context

FALLBACK_LATENCY_MS = 4000  # ~4s typical Haiku call


def _resolve_model(agent: str, overrides: dict[str, str]) -> tuple[str, str]:
    spec = overrides.get(agent)
    if spec and ":" in spec:
        provider, model = spec.split(":", 1)
        return provider, model
    return DEFAULT_MODELS.get(agent) or DEFAULT_MODELS.get("buffett") or ("anthropic", "")


def _trading_day_count(
    universe: list[str], start: dt.date, end: dt.date,
) -> int:
    return (
        DailyBar.objects.filter(ticker__in=universe, date__gte=start, date__lte=end)
        .values_list("date", flat=True).distinct().count()
    )


def _trading_days(
    universe: list[str], start: dt.date, end: dt.date,
) -> list[dt.date]:
    return list(
        DailyBar.objects.filter(ticker__in=universe, date__gte=start, date__lte=end)
        .values_list("date", flat=True).distinct().order_by("date")
    )


def _avg_cost_for(agent: str, model: str) -> float | None:
    rows = list(
        LLMCall.objects.filter(agent_name=agent, model=model)
        .order_by("-id").values_list("cost_usd", flat=True)[:50]
    )
    if not rows:
        return None
    return float(mean(float(r) for r in rows))


def _avg_latency_for(agent: str, model: str) -> float | None:
    rows = list(
        LLMCall.objects.filter(agent_name=agent, model=model)
        .order_by("-id").values_list("latency_ms", flat=True)[:50]
    )
    if not rows:
        return None
    return float(mean(int(r or 0) for r in rows))


def _per_call_cost(agent: str, provider: str, model: str, is_persona: bool) -> float:
    avg = _avg_cost_for(agent, model)
    if avg is not None:
        return avg
    # ModelEntry catalog is the source of truth (includes the free OpenRouter
    # rows whose price is 0). The static PRICING dict is only a bootstrap for
    # bare models that don't have a provider prefix.
    price = lookup_catalog_price(f"{provider}:{model}") if provider else None
    if price is None:
        price = PRICING.get(model)
    if price is not None:
        # Coarse: 1800 in / 700 out tokens for non-personas, 3000/1000 for personas.
        toks_in, toks_out = (3000, 1000) if is_persona else (1800, 700)
        return (
            toks_in / 1_000_000 * price.input_per_mtok
            + toks_out / 1_000_000 * price.output_per_mtok
        )
    return FALLBACK_PERSONA_CALL if is_persona else FALLBACK_COST_PER_CALL.get(agent, 0.005)


def estimate_cost(
    *,
    universe: Iterable[str],
    start_date: dt.date,
    end_date: dt.date,
    rebalance_frequency: str = "weekly",
    personas: list[str] | None = None,
    model_overrides: dict[str, str] | None = None,
    max_budget_usd: Decimal | float | None = None,
) -> dict[str, Any]:
    universe_list = list(universe)
    overrides = model_overrides or {}
    selected_personas = list(personas or ALL_PERSONAS)
    days = _trading_days(universe_list, start_date, end_date)
    rebal_days = sorted(rebalance_dates_for(days, rebalance_frequency))
    n_invocations = len(rebal_days) * len(universe_list)

    agent_lines: list[dict[str, Any]] = []
    total_cost = 0.0
    max_latency_per_inv = 0.0  # parallel fan-out → longest single call dominates
    sequential_latency_per_inv = 0.0  # personas + analytical + pipeline are gated

    agents_with_role = [
        (a, False) for a in ANALYTICAL_AGENTS
    ] + [
        (p, True) for p in selected_personas
    ] + [
        (a, False) for a in PIPELINE_AGENTS
    ]
    for agent, is_persona in agents_with_role:
        provider, model = _resolve_model(agent, overrides)
        cpc = _per_call_cost(agent, provider, model, is_persona)
        agent_cost = cpc * n_invocations
        total_cost += agent_cost
        lat = _avg_latency_for(agent, model) or FALLBACK_LATENCY_MS
        max_latency_per_inv = max(max_latency_per_inv, lat)
        sequential_latency_per_inv += lat
        agent_lines.append({
            "agent": agent, "model": f"{provider}:{model}",
            "per_call_usd": round(cpc, 5),
            "total_usd": round(agent_cost, 4),
        })

    # Wall-clock model: analytical+personas fan out in parallel within a single
    # invocation; pipeline agents run after. Approximate as max(analytical) +
    # max(personas) + sum(pipeline). This is rough; the conservative form
    # (sum of all per-agent latencies) is the upper bound.
    est_minutes_optimistic = (n_invocations * max_latency_per_inv) / 1000 / 60
    est_minutes_upper = (n_invocations * sequential_latency_per_inv) / 1000 / 60

    cap = float(max_budget_usd) if max_budget_usd is not None else None
    return {
        "n_trading_days": len(days),
        "n_rebalance_days": len(rebal_days),
        "n_universe": len(universe_list),
        "n_invocations": n_invocations,
        "n_llm_calls": n_invocations * len(agents_with_role),
        "est_total_usd": round(total_cost, 2),
        "est_minutes_optimistic": round(est_minutes_optimistic, 1),
        "est_minutes_upper": round(est_minutes_upper, 1),
        "budget_cap_usd": cap,
        "exceeds_budget": (cap is not None and total_cost > cap),
        "by_agent": agent_lines,
    }
