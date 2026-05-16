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

    # Outputs accumulated by nodes
    fundamentals: dict[str, Any]
    technicals: dict[str, Any]
    buffett: dict[str, Any]
    decision: dict[str, Any]


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
