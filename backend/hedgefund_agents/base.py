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
    user_id: int  # owner of the run — lets agent nodes resolve per-user
                  # provider keys (P3-C §12.6: needed so get_llm("ollama", state=state)
                  # picks up pk.ollama_host instead of the localhost default).
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

    # Flavor tag for prompt/PM rule switching. Set by the dispatcher per task.
    # When "sector_rotation", personas get the sector-context prompt prefix and
    # the PM applies the screener-led / council-as-veto rule.
    flavor: str
    sector_veto_log: list[dict[str, Any]]
    # Per-candidate veto record emitted by run_portfolio_manager under
    # flavor=="sector_rotation"; finalize_cycle collects these into
    # PortfolioTarget.sector_veto_log.
    sector_veto_entry: dict[str, Any]
    # Sector-rotation context that personas read from state (set by dispatcher).
    sector: str
    theme: str
    use_llm_cache: bool  # backtests: read/write LLMResponseCache (L2)
    backtest_id: int  # for cache scoping / telemetry

    # P3-prereq-5 WS-C/WS-G: investor profile injected by execute_run (ad-hoc
    # runs always, opted-in strategy cycles). Empty dict {} ⇒ no
    # personalization (no-op in every consumer). NEVER set by the backtest
    # engine — see apps/backtests/tests for the import-boundary guard.
    investor_profile: dict[str, Any]


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
