"""Fundamentals agent. Loads quarterly statements (point-in-time correct)
and asks the LLM to summarize quality into a structured output.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from .._persist import record_llm_call
from ..base import AgentState, pick_model
from ..llm.client import Message
from ..llm.structured import call_structured
from ..outputs import FundamentalsOutput
from ..registry import DEFAULT_MODELS, get_llm

METRICS = [
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "free_cash_flow",
    "total_debt",
    "total_equity",
    "total_assets",
]


def _build_table(rows: list) -> str:
    by_metric: dict[str, list[tuple[str, Decimal]]] = defaultdict(list)
    for r in rows:
        by_metric[r.metric].append((r.period_end.isoformat(), r.value))
    lines = []
    for metric, series in by_metric.items():
        series.sort(reverse=True)
        formatted = ", ".join(f"{d}={v:.2f}" for d, v in series[:8])
        lines.append(f"- {metric}: {formatted}")
    return "\n".join(lines)


def run_fundamentals(state: AgentState) -> AgentState:
    ticker = state["ticker"]
    as_of = state["as_of_date"]
    rows = state["data_provider"].get_fundamentals(
        ticker, METRICS, as_of=as_of, lookback_quarters=8
    )
    table = _build_table(rows)

    provider, model = pick_model(state, "fundamentals", DEFAULT_MODELS["fundamentals"])
    client = get_llm(provider)
    system = (
        "You are a quantitative equity analyst. Given the company's last 8 "
        "quarters of fundamentals, compute trailing ratios and produce a "
        "concise quality assessment. Use ONLY the data provided."
    )
    user = (
        f"Ticker: {ticker}\nAs-of date: {as_of.isoformat()}\n\n"
        f"Quarterly fundamentals (most recent first):\n{table}\n\n"
        "Estimate 3-year revenue CAGR (use 12 trailing quarters worth of revenue), "
        "trailing-twelve-months gross/operating/FCF margins, approximate ROIC, "
        "debt/equity, and assign quality_score 0-100. Provide a 1-2 sentence note."
    )
    parsed, resp = call_structured(
        client,
        model=model,
        schema=FundamentalsOutput,
        messages=[Message("system", system), Message("user", user)],
        max_tokens=4096,
    )
    record_llm_call(run_id=state.get("run_id"), agent_name="fundamentals", resp=resp)
    return {"fundamentals": parsed.model_dump()}  # type: ignore[return-value]
