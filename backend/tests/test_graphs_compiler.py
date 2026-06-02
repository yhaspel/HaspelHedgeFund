"""P4c compiler equivalence + registry capability + templates.

The headline test: compile_graph(create_canonical_council_version()) is
structurally identical to build_council_graph(). Pure Python — compiling a
LangGraph does not invoke any agent/LLM.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.graphs.compiler import compile_graph
from apps.graphs.registry import ALL_AGENTS, EDITABLE_TYPES, SELECTABLE_TYPES, TAIL_TYPES
from apps.graphs.templates import (
    STARTERS,
    build_version_json,
    create_canonical_council_version,
)
from hedgefund_agents.graphs.council import build_council_graph
from hedgefund_agents.portfolio.cio import run_cio


def _version(j, tail=None):
    return SimpleNamespace(id=0, nodes=j["nodes"], edges=j["edges"], tail_models=tail or {})


def _topo(compiled):
    g = compiled.get_graph()
    nodes = set(g.nodes.keys())
    edges = {(e.source, e.target) for e in g.edges}
    return nodes, edges


# ---- registry capability matrix ---------------------------------------

def test_registry_counts():
    assert len(EDITABLE_TYPES) == 14  # 6 analytical + 8 personas
    assert sorted(TAIL_TYPES) == ["cio", "portfolio_manager", "risk_manager"]
    assert len(ALL_AGENTS) == 17


def test_state_key_divergence():
    assert ALL_AGENTS["risk_manager"].state_key == "risk"
    assert ALL_AGENTS["portfolio_manager"].state_key == "decision"
    assert ALL_AGENTS["cio"].state_key == "cio"
    # everything else: state_key == agent_name
    for name, spec in ALL_AGENTS.items():
        if name not in ("risk_manager", "portfolio_manager"):
            assert spec.state_key == name


def test_pm_not_selectable_and_not_editable():
    pm = ALL_AGENTS["portfolio_manager"]
    assert pm.model_selectable is False
    assert pm.editable is False
    assert pm.default_model_key is None
    assert "portfolio_manager" not in SELECTABLE_TYPES


def test_tail_selectable_set():
    assert ALL_AGENTS["risk_manager"].model_selectable
    assert ALL_AGENTS["cio"].model_selectable
    # tail nodes are never editable/placeable
    for t in TAIL_TYPES:
        assert ALL_AGENTS[t].editable is False


# ---- compiler equivalence ----------------------------------------------

def test_canonical_compiles_equivalent_to_build_council():
    j, _tail = create_canonical_council_version()
    compiled = compile_graph(_version(j))
    expected = build_council_graph()
    assert _topo(compiled) == _topo(expected)


def test_canonical_topology_shape():
    j, _tail = create_canonical_council_version()
    nodes, edges = _topo(compile_graph(_version(j)))
    # 3 structural + 6 analytical + 8 persona + 3 tail = 20 real nodes
    for expected in ("entry", "analytical_join", "persona_join",
                     "risk_manager", "portfolio_manager", "cio",
                     "fundamentals", "macro", "news_digest", "buffett", "lynch"):
        assert expected in nodes, expected
    # cio is the finish; PM precedes it
    assert ("portfolio_manager", "cio") in edges
    assert any(src == "cio" for src, _ in edges)  # cio -> __end__


def test_subset_single_persona_topology():
    j, _tail = build_version_json(["fundamentals", "valuation"], ["buffett"])
    nodes, edges = _topo(compile_graph(_version(j)))
    assert "buffett" in nodes
    assert "munger" not in nodes
    assert "fundamentals" in nodes and "technicals" not in nodes
    # tail still injected
    assert {"risk_manager", "portfolio_manager", "cio"} <= nodes
    assert ("entry", "fundamentals") in edges
    assert ("analytical_join", "buffett") in edges


def test_compiler_tolerates_unknown_node_type():
    j, _tail = build_version_json(["fundamentals"], ["buffett"])
    j["nodes"].append({"id": "future_agent", "type": "future_agent",
                       "model_id": "x:y", "config": {}})
    # should not raise; unknown node dropped with a logged warning
    nodes, _edges = _topo(compile_graph(_version(j)))
    assert "future_agent" not in nodes
    assert {"fundamentals", "buffett", "risk_manager", "cio"} <= nodes


# ---- disable_cio runtime no-op (not a structural removal) --------------

def test_disable_cio_is_runtime_noop():
    # CIO is always present in the graph; disable_cio makes run_cio return {}.
    assert run_cio({"disable_cio": True}) == {}
    j, _tail = create_canonical_council_version()
    nodes, _edges = _topo(compile_graph(_version(j)))
    assert "cio" in nodes  # structure unchanged regardless of disable_cio


# ---- templates ---------------------------------------------------------

def test_all_starters_compile_and_are_well_formed():
    from apps.graphs.validators import validate_graph
    names = {s["name"] for s in STARTERS}
    assert names == {"Council classic", "Sector rotation",
                     "Single persona (Buffett)", "Value only"}
    for starter in STARTERS:
        j, tail = build_version_json(starter["analytical"], starter["personas"])
        res = validate_graph(j["nodes"], j["edges"], tail)
        assert res.is_valid, (starter["name"], [e.message for e in res.errors])
        # every starter compiles
        compile_graph(_version(j, tail))


@pytest.mark.parametrize("name", ["risk_manager", "cio"])
def test_canonical_tail_models_defaulted(name):
    _j, tail = create_canonical_council_version()
    assert name in tail and tail[name]  # provider:model present
    assert "portfolio_manager" not in tail
