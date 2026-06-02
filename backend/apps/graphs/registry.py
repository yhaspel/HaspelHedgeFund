"""Canonical agent registry for the visual graph editor (P4c).

The agent runtime scatters its node definitions across several modules:
`personas.PERSONA_NODES`, `graphs.council.ANALYTICAL_NODES`, and the hardcoded
`run_risk_manager` / `run_portfolio_manager` / `run_cio` tail. This module
unifies them into ONE table — `ALL_AGENTS` — that the compiler, validator, and
editor API all read, so there is a single source of truth for:

  * which node types exist and what `kind` they are,
  * the `model_overrides` key each selectable node honors (== `agent_name`),
  * the `AgentState` output key each node writes (`state_key`, which DIVERGES
    from `agent_name` for `risk_manager`→"risk" and `portfolio_manager`→
    "decision" — see council.py:84-85), and
  * whether a node is user-editable (placeable on the canvas) and whether it
    exposes a model picker.

There is no `agent_factory`: agent nodes are `run_*(state) -> dict` functions
and the model is chosen at RUNTIME from `state["model_overrides"]` via
`pick_model`, never at construction time. So `NodeSpec` carries the run
function by reference and the model is delivered later through `model_overrides`.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from hedgefund_agents.graphs.council import ANALYTICAL_NODES
from hedgefund_agents.personas import PERSONA_NODES
from hedgefund_agents.portfolio.cio import run_cio
from hedgefund_agents.portfolio.portfolio_manager import run_portfolio_manager
from hedgefund_agents.registry import DEFAULT_MODELS
from hedgefund_agents.risk.risk_manager import run_risk_manager

# Schema version stamped on saved graph JSON so old graphs keep loading when the
# node vocabulary evolves (Risk 4: tolerant lookup, drop-unknown-with-warning).
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class NodeSpec:
    agent_name: str  # canvas node type AND the model_overrides key pick_model() reads
    run_fn: Callable  # run_*(state) -> dict
    kind: str  # "analytical" | "persona" | "risk" | "portfolio" | "cio"
    state_key: str  # the AgentState output key (2nd arg to wrap_backtest_tolerant)
    model_selectable: bool  # True iff the node calls pick_model() and honors overrides
    editable: bool  # True iff the user may place/delete/rewire it on the canvas
    default_model_key: str | None  # "provider:model", or None for the deterministic PM


def _default_key(agent_name: str) -> str | None:
    """Effective runtime default model as a "provider:model" string.

    Sourced from DEFAULT_MODELS (which already reflects the BLOCK_ANTHROPIC dev
    flip), so the editor pre-selects exactly the model the runtime would use
    absent an override. None when the agent has no LLM.
    """
    pair = DEFAULT_MODELS.get(agent_name)
    if pair is None:
        return None
    provider, model = pair
    return f"{provider}:{model}"


def _build_all_agents() -> dict[str, NodeSpec]:
    specs: dict[str, NodeSpec] = {}

    # Analytical tier (6): fundamentals, technicals, valuation, sentiment,
    # macro, news_digest. state_key == agent_name for all of these.
    for name, fn in ANALYTICAL_NODES.items():
        specs[name] = NodeSpec(
            agent_name=name, run_fn=fn, kind="analytical", state_key=name,
            model_selectable=True, editable=True, default_model_key=_default_key(name),
        )

    # Personas (8). override key == persona name == state_key.
    for name, fn in PERSONA_NODES.items():
        specs[name] = NodeSpec(
            agent_name=name, run_fn=fn, kind="persona", state_key=name,
            model_selectable=True, editable=True, default_model_key=_default_key(name),
        )

    # Implicit, non-editable terminal tail (D2). risk_manager and cio are
    # model-selectable (their model lives in version.tail_models); the
    # portfolio_manager is deterministic and exposes no picker.
    specs["risk_manager"] = NodeSpec(
        agent_name="risk_manager", run_fn=run_risk_manager, kind="risk",
        state_key="risk", model_selectable=True, editable=False,
        default_model_key=_default_key("risk_manager"),
    )
    specs["portfolio_manager"] = NodeSpec(
        agent_name="portfolio_manager", run_fn=run_portfolio_manager, kind="portfolio",
        state_key="decision", model_selectable=False, editable=False,
        default_model_key=None,
    )
    specs["cio"] = NodeSpec(
        agent_name="cio", run_fn=run_cio, kind="cio",
        state_key="cio", model_selectable=True, editable=False,
        default_model_key=_default_key("cio"),
    )
    return specs


ALL_AGENTS: dict[str, NodeSpec] = _build_all_agents()

# Synthetic pass-through nodes the compiler injects; never stored in version.nodes.
STRUCTURAL_TYPES: frozenset[str] = frozenset({"entry", "analytical_join", "persona_join"})

# The implicit tail (D2) — injected by the compiler, never stored in version.nodes.
TAIL_TYPES: frozenset[str] = frozenset({"risk_manager", "portfolio_manager", "cio"})

# Node types the user MAY place on the canvas (palette items).
ANALYTICAL_TYPES: frozenset[str] = frozenset(
    n for n, s in ALL_AGENTS.items() if s.kind == "analytical"
)
PERSONA_TYPES: frozenset[str] = frozenset(
    n for n, s in ALL_AGENTS.items() if s.kind == "persona"
)
EDITABLE_TYPES: frozenset[str] = ANALYTICAL_TYPES | PERSONA_TYPES

# Every node (editable or tail) that honors model_overrides.
SELECTABLE_TYPES: frozenset[str] = frozenset(
    n for n, s in ALL_AGENTS.items() if s.model_selectable
)
