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
# What the council graph ACTUALLY issues per (ticker, rebalance-day) prime:
#   * the 4 analytical nodes + every selected persona + news_digest +
#     risk_manager                                   → one call each, per prime
#   * macro                                          → ONE call per unique
#     as_of_date (MacroSnapshot cache, macro_agent.py) — NOT per ticker
#   * portfolio_manager                              → ZERO LLM calls; the PM
#     node is a deterministic aggregate() (portfolio_manager.py)
#   * cio                                            → one per prime, and only
#     when the run opts in (Backtest.disable_cio=False; default is skipped)
# Billing macro per ticker and portfolio_manager at all overstated every
# estimate by (2 − 1/n_universe) calls per prime.
PER_PRIME_PIPELINE_AGENTS = ["news_digest", "risk_manager"]
PER_DAY_AGENTS = ["macro"]
NON_LLM_AGENTS = ["portfolio_manager"]

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
    disable_cio: bool = True,
) -> dict[str, Any]:
    universe_list = [str(t) for t in universe]
    if not isinstance(model_overrides, dict) and model_overrides is not None:
        raise TypeError("model_overrides must be an object mapping agent -> 'provider:model'")
    overrides = {
        str(k): v for k, v in (model_overrides or {}).items() if isinstance(v, str)
    }
    selected_personas = [str(p) for p in (personas or ALL_PERSONAS)]
    days = _trading_days(universe_list, start_date, end_date)
    rebal_days = sorted(rebalance_dates_for(days, rebalance_frequency))
    # One "prime" = one (ticker, rebalance-day) graph invocation. The prime phase
    # walks the WHOLE master window once, so this is the unique ticker-day count
    # across every fold — never a per-fold constant.
    n_primes = len(rebal_days) * len(universe_list)
    n_invocations = n_primes

    agent_lines: list[dict[str, Any]] = []
    total_cost = 0.0
    max_latency_per_inv = 0.0  # parallel fan-out → longest single call dominates
    sequential_latency_per_inv = 0.0  # personas + analytical + pipeline are gated

    # (agent, is_persona, n_calls)
    agents_with_role: list[tuple[str, bool, int]] = (
        [(a, False, n_primes) for a in ANALYTICAL_AGENTS]
        + [(p, True, n_primes) for p in selected_personas]
        + [(a, False, n_primes) for a in PER_PRIME_PIPELINE_AGENTS]
        + [(a, False, len(rebal_days)) for a in PER_DAY_AGENTS]
    )
    if not disable_cio:
        agents_with_role.append(("cio", False, n_primes))
    n_llm_calls = sum(n for _a, _p, n in agents_with_role)

    for agent, is_persona, n_calls in agents_with_role:
        provider, model = _resolve_model(agent, overrides)
        cpc = _per_call_cost(agent, provider, model, is_persona)
        agent_cost = cpc * n_calls
        total_cost += agent_cost
        lat = _avg_latency_for(agent, model) or FALLBACK_LATENCY_MS
        max_latency_per_inv = max(max_latency_per_inv, lat)
        # Amortize an agent that does not run on every prime (macro is cached per
        # as_of_date) over the primes it does not gate.
        sequential_latency_per_inv += lat * (n_calls / max(1, n_primes))
        agent_lines.append({
            "agent": agent, "model": f"{provider}:{model}",
            "per_call_usd": round(cpc, 5),
            "n_calls": n_calls,
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
        # n_primes == n_invocations; both are exposed because the UI labels the
        # prime phase separately from the per-agent call count.
        "n_primes": n_primes,
        "n_invocations": n_invocations,
        "n_llm_calls": n_llm_calls,
        "est_total_usd": round(total_cost, 2),
        "est_minutes_optimistic": round(est_minutes_optimistic, 1),
        "est_minutes_upper": round(est_minutes_upper, 1),
        "budget_cap_usd": cap,
        "exceeds_budget": (cap is not None and total_cost > cap),
        "by_agent": agent_lines,
    }
