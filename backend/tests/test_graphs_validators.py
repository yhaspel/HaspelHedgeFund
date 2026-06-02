"""P4c graph validator rules — positive + negative for every rule.

Pure Python (no DB): validators operate on draft dicts.
"""
from __future__ import annotations

import copy

from apps.graphs.templates import create_canonical_council_version
from apps.graphs.validators import (
    _has_cycle,
    canonical_edges,
    fill_default_models,
    validate_graph,
)


def _canonical_draft():
    j, tail = create_canonical_council_version()
    return copy.deepcopy(j["nodes"]), copy.deepcopy(j["edges"]), copy.deepcopy(tail)


def _rules(issues):
    return {i.rule for i in issues}


# ---- positive ----------------------------------------------------------

def test_canonical_council_is_valid():
    nodes, edges, tail = _canonical_draft()
    res = validate_graph(nodes, edges, tail)
    assert res.is_valid, _rules(res.errors)
    assert res.status == "valid"
    assert not res.warnings  # every node has a default model_id


def test_minimal_one_analytical_one_persona_is_valid():
    nodes = [
        {"id": "fundamentals", "type": "fundamentals", "model_id": "anthropic:x"},
        {"id": "buffett", "type": "buffett", "model_id": "anthropic:x"},
    ]
    edges = canonical_edges(nodes)
    res = validate_graph(nodes, edges)
    assert res.is_valid, _rules(res.errors)


# ---- node-set errors ---------------------------------------------------

def test_structural_type_in_nodes_errors():
    nodes, edges, tail = _canonical_draft()
    nodes.append({"id": "entry", "type": "entry"})
    res = validate_graph(nodes, edges, tail)
    assert "structural_in_nodes" in _rules(res.errors)


def test_tail_type_in_nodes_errors():
    nodes, edges, tail = _canonical_draft()
    nodes.append({"id": "risk_manager", "type": "risk_manager"})
    res = validate_graph(nodes, edges, tail)
    assert "tail_in_nodes" in _rules(res.errors)


def test_unknown_type_errors():
    nodes = [
        {"id": "buffett", "type": "buffett", "model_id": "anthropic:x"},
        {"id": "wizard", "type": "wizard"},
    ]
    res = validate_graph(nodes, canonical_edges(nodes))
    assert "unknown_type" in _rules(res.errors)


def test_duplicate_type_errors():
    nodes = [
        {"id": "buffett", "type": "buffett", "model_id": "a:b"},
        {"id": "buffett", "type": "buffett", "model_id": "a:b"},
        {"id": "fundamentals", "type": "fundamentals", "model_id": "a:b"},
    ]
    res = validate_graph(nodes, canonical_edges(nodes))
    assert "duplicate_type" in _rules(res.errors)


def test_id_must_equal_type_errors():
    nodes = [
        {"id": "node-1", "type": "buffett", "model_id": "a:b"},
        {"id": "fundamentals", "type": "fundamentals", "model_id": "a:b"},
    ]
    res = validate_graph(nodes, canonical_edges([{"id": "buffett", "type": "buffett"},
                                                 {"id": "fundamentals", "type": "fundamentals"}]))
    assert "id_must_equal_type" in _rules(res.errors)


def test_no_persona_errors():
    nodes = [{"id": "fundamentals", "type": "fundamentals", "model_id": "a:b"}]
    res = validate_graph(nodes, canonical_edges(nodes))
    assert "no_persona" in _rules(res.errors)


def test_no_analytical_errors():
    nodes = [{"id": "buffett", "type": "buffett", "model_id": "a:b"}]
    res = validate_graph(nodes, canonical_edges(nodes))
    assert "no_analytical" in _rules(res.errors)


# ---- edge errors -------------------------------------------------------

def test_orphan_node_errors_on_missing_edge():
    nodes, edges, tail = _canonical_draft()
    # drop both edges touching 'buffett' → orphan
    edges = [e for e in edges if "buffett" not in (e["from"], e["to"])]
    res = validate_graph(nodes, edges, tail)
    rules = _rules(res.errors)
    assert "orphan_node" in rules


def test_non_canonical_edge_errors():
    nodes = [
        {"id": "fundamentals", "type": "fundamentals", "model_id": "a:b"},
        {"id": "buffett", "type": "buffett", "model_id": "a:b"},
        {"id": "munger", "type": "munger", "model_id": "a:b"},
    ]
    edges = canonical_edges(nodes)
    edges.append({"from": "buffett", "to": "munger"})  # persona→persona, illegal
    res = validate_graph(nodes, edges)
    assert "edge_not_canonical" in _rules(res.errors)


def test_edge_unknown_endpoint_errors():
    nodes = [
        {"id": "fundamentals", "type": "fundamentals", "model_id": "a:b"},
        {"id": "buffett", "type": "buffett", "model_id": "a:b"},
    ]
    edges = canonical_edges(nodes)
    edges.append({"from": "buffett", "to": "ghost"})
    res = validate_graph(nodes, edges)
    assert "edge_unknown_endpoint" in _rules(res.errors)


def test_cycle_helper_detects_cycle():
    assert _has_cycle({("a", "b"), ("b", "c"), ("c", "a")})
    assert not _has_cycle({("a", "b"), ("b", "c")})


# ---- tail_models -------------------------------------------------------

def test_tail_models_invalid_key_errors():
    nodes, edges, tail = _canonical_draft()
    tail["portfolio_manager"] = "a:b"  # PM is not selectable
    res = validate_graph(nodes, edges, tail)
    assert "tail_model_invalid_key" in _rules(res.errors)


def test_tail_models_malformed_value_warns():
    nodes, edges, tail = _canonical_draft()
    tail["risk_manager"] = "no-colon-here"
    res = validate_graph(nodes, edges, tail)
    assert res.is_valid  # still valid (warning only)
    assert "tail_model_malformed" in _rules(res.warnings)


# ---- warnings ----------------------------------------------------------

def test_missing_model_id_warns_but_valid():
    nodes, edges, tail = _canonical_draft()
    for n in nodes:
        if n["type"] == "buffett":
            n.pop("model_id", None)
    res = validate_graph(nodes, edges, tail)
    assert res.is_valid
    assert "missing_model_id" in _rules(res.warnings)


def test_news_digest_small_context_warns():
    nodes = [
        {"id": "news_digest", "type": "news_digest", "model_id": "x:tiny"},
        {"id": "buffett", "type": "buffett", "model_id": "a:b"},
    ]
    res = validate_graph(nodes, canonical_edges(nodes),
                         context_window_lookup=lambda mid: 8000)
    assert "news_digest_small_context" in _rules(res.warnings)
    # large context → no warning
    res2 = validate_graph(nodes, canonical_edges(nodes),
                          context_window_lookup=lambda mid: 200000)
    assert "news_digest_small_context" not in _rules(res2.warnings)


def test_news_digest_lookup_failure_never_blocks():
    nodes = [
        {"id": "news_digest", "type": "news_digest", "model_id": "x:tiny"},
        {"id": "buffett", "type": "buffett", "model_id": "a:b"},
    ]

    def boom(_mid):
        raise RuntimeError("catalog down")

    res = validate_graph(nodes, canonical_edges(nodes), context_window_lookup=boom)
    assert res.is_valid  # swallowed; no warning, no crash
    assert "news_digest_small_context" not in _rules(res.warnings)


# ---- fill_default_models ----------------------------------------------

def test_fill_default_models_fills_missing():
    nodes = [
        {"id": "buffett", "type": "buffett"},  # no model_id
        {"id": "fundamentals", "type": "fundamentals", "model_id": "keep:me"},
    ]
    out_nodes, out_tail = fill_default_models(nodes, {})
    by_type = {n["type"]: n for n in out_nodes}
    assert by_type["buffett"]["model_id"]  # filled from default
    assert by_type["fundamentals"]["model_id"] == "keep:me"  # preserved
    assert out_tail["risk_manager"] and out_tail["cio"]  # tail defaults filled
    assert "portfolio_manager" not in out_tail  # deterministic, never filled
