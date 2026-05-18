"""Agent base + shared state.

`AgentState` is the dict that flows through every node of a LangGraph.
Each node reads what it needs and writes its output back under a stable
key (`fundamentals`, `technicals`, `buffett`, `decision`).
"""
from __future__ import annotations

import datetime as dt
from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    # Input
    ticker: str
    as_of_date: dt.date
    run_id: int  # FK target for LLMCall rows
    model_overrides: dict[str, str]

    # Wiring
    data_provider: Any
    filings_provider: Any
    llm_clients: dict[str, Any]  # provider name -> client instance

    # Optional inputs
    portfolio: Any
    risk_limits: Any
    news: Any

    # Outputs accumulated by nodes (analytical)
    fundamentals: dict[str, Any]
    technicals: dict[str, Any]
    valuation: dict[str, Any]
    sentiment: dict[str, Any]
    macro: dict[str, Any]
    news_digest: dict[str, Any]

    # Persona outputs
    buffett: dict[str, Any]
    munger: dict[str, Any]
    graham: dict[str, Any]
    wood: dict[str, Any]
    druckenmiller: dict[str, Any]
    burry: dict[str, Any]
    damodaran: dict[str, Any]
    lynch: dict[str, Any]

    # Risk + PM + CIO
    risk: dict[str, Any]
    decision: dict[str, Any]
    pm_decision: dict[str, Any]  # deterministic PM, preserved for audit
    cio: dict[str, Any]
    disable_cio: bool  # backtests: skip CIO for reproducibility
    pm_config: dict[str, Any]  # backtest sweep overrides for portfolio_manager
    trailing_returns: list[float]  # trailing daily returns for vol-target sizing
    use_llm_cache: bool  # backtests: read/write LLMResponseCache (L2)
    backtest_id: int  # for cache scoping / telemetry


def pick_model(state: AgentState, agent_name: str, default: tuple[str, str]) -> tuple[str, str]:
    """Returns (provider, model) for an agent, honoring per-run overrides.

    Override format: state["model_overrides"][agent_name] = "provider:model".
    """
    overrides = state.get("model_overrides") or {}
    override = overrides.get(agent_name)
    if override and ":" in override:
        provider, model = override.split(":", 1)
        return provider, model
    return default
