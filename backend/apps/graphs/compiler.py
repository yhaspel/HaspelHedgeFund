"""Compile a saved AgentGraphVersion into a runnable LangGraph (P4c).

`compile_graph` structurally reproduces `hedgefund_agents/graphs/council.py`:
the synthetic entry / analytical_join / persona_join pass-throughs, the
mandatory `wrap_backtest_tolerant` wrapping on every agent node (with the
correct, sometimes-divergent `state_key`), and the always-injected
`risk_manager → portfolio_manager → cio → END` tail.

The wiring is DERIVED from the node set's kinds — exactly like
`build_council_graph` wires from its `ANALYTICAL_NODES` / `selected` lists —
rather than replayed from the stored `edges`. This makes runtime structure
invariant to a malformed canvas and guarantees equivalence to council.py.

`disable_cio` is NOT a compile-time branch: `run_cio` no-ops at runtime when
`state["disable_cio"]` is set (cio.py:49), so the structure is unconditional.
Per-node model selection is delivered separately by populating
`state["model_overrides"]` at submission (see serializers) — not here.
"""
from __future__ import annotations

import logging

from django.conf import settings
from langgraph.graph import END, StateGraph

from hedgefund_agents.base import AgentState
from hedgefund_agents.graphs._node_fallback import wrap_backtest_tolerant
from hedgefund_agents.graphs.council import _entry, _join
from hedgefund_agents.personas import ALL_PERSONAS

from .registry import ALL_AGENTS

log = logging.getLogger(__name__)


def compile_graph(version):
    """Compile an AgentGraphVersion → a compiled LangGraph StateGraph."""
    sg = StateGraph(AgentState)
    sg.add_node("entry", _entry)
    sg.add_node("analytical_join", _join)
    sg.add_node("persona_join", _join)

    analytical: list = []
    personas: list = []
    for node in version.nodes or []:
        spec = ALL_AGENTS.get(node.get("type"))
        if spec is None:
            # Risk 4: tolerate unknown node types from a newer schema by
            # dropping them with a warning rather than refusing to load.
            log.warning("graph %s: dropping unknown node type %r",
                        getattr(version, "id", "?"), node.get("type"))
            continue
        if spec.kind == "analytical":
            analytical.append(spec)
        elif spec.kind == "persona":
            personas.append(spec)
        else:
            # Tail/structural types should never reach version.nodes (validator
            # Error); drop defensively.
            log.warning("graph %s: ignoring non-editable node type %r in nodes",
                        getattr(version, "id", "?"), node.get("type"))

    for spec in analytical:
        sg.add_node(spec.agent_name, wrap_backtest_tolerant(spec.run_fn, spec.state_key))
        sg.add_edge("entry", spec.agent_name)
        sg.add_edge(spec.agent_name, "analytical_join")

    for spec in personas:
        sg.add_node(spec.agent_name, wrap_backtest_tolerant(spec.run_fn, spec.state_key))
        sg.add_edge("analytical_join", spec.agent_name)
        sg.add_edge(spec.agent_name, "persona_join")

    # Implicit fixed tail (D2) — ALWAYS injected, identical to council.py:84-102.
    rm = ALL_AGENTS["risk_manager"]
    pm = ALL_AGENTS["portfolio_manager"]
    cio = ALL_AGENTS["cio"]
    sg.add_node("risk_manager", wrap_backtest_tolerant(rm.run_fn, rm.state_key))
    sg.add_node("portfolio_manager", wrap_backtest_tolerant(pm.run_fn, pm.state_key))
    sg.add_node("cio", wrap_backtest_tolerant(cio.run_fn, cio.state_key))

    sg.set_entry_point("entry")
    sg.add_edge("persona_join", "risk_manager")
    sg.add_edge("risk_manager", "portfolio_manager")
    sg.add_edge("portfolio_manager", "cio")
    sg.add_edge("cio", END)
    return sg.compile()


def resolve_graph(obj):
    """Resolve the graph for a Run or Backtest.

    Guarded by ENABLE_DB_GRAPHS. When the flag is on and the object references
    an AgentGraphVersion, compile it; on compile failure, fall back to the
    hardcoded council.py and log (unless GRAPH_FALLBACK_TO_HARDCODED is off, in
    which case re-raise). `disable_cio` continues to flow via state, unchanged.
    """
    # Function-local import so a test (or runtime) monkeypatch of
    # `hedgefund_agents.graphs.council.build_council_graph` is honored on the
    # fallback path — exactly as the original build sites imported it locally.
    from hedgefund_agents.graphs.council import build_council_graph

    if getattr(settings, "ENABLE_DB_GRAPHS", False) and getattr(obj, "graph_version_id", None):
        try:
            return compile_graph(obj.graph_version)
        except Exception:
            log.exception("graph %s failed to compile", getattr(obj, "graph_version_id", "?"))
            if not getattr(settings, "GRAPH_FALLBACK_TO_HARDCODED", True):
                raise
    return build_council_graph(personas=list(getattr(obj, "personas", None) or ALL_PERSONAS))
