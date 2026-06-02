"""P4c run/backtest/schedule integration: submission flatten + resolve_graph
+ fallback. Tests serializers/resolver directly to avoid triggering the eager
Celery execute_run (which would make real LLM calls)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from apps.backtests.serializers import BacktestCreateSerializer
from apps.graphs.compiler import compile_graph, resolve_graph
from apps.graphs.models import AgentGraph, AgentGraphVersion
from apps.graphs.submission import model_overrides_for_version, personas_for_version
from apps.graphs.templates import create_canonical_council_version
from apps.runs.models import Run
from apps.runs.serializers import RunCreateSerializer
from hedgefund_agents.graphs.council import build_council_graph

User = get_user_model()
pytestmark = pytest.mark.django_db

CUSTOM = "anthropic:claude-haiku-4-5-20251001"


def _ctx(user):
    return {"request": SimpleNamespace(user=user)}


def _make_version(user, *, valid=True, buffett_model=CUSTOM, name="G"):
    j, tail = create_canonical_council_version()
    for n in j["nodes"]:
        if n["type"] == "buffett":
            n["model_id"] = buffett_model
    graph = AgentGraph.objects.create(user=user, name=name)
    return AgentGraphVersion.objects.create(
        graph=graph, version=1, nodes=j["nodes"], edges=j["edges"], tail_models=tail,
        validation_status=AgentGraphVersion.VALID if valid else AgentGraphVersion.DRAFT_INVALID,
        created_by=user,
    )


def _topo(compiled):
    g = compiled.get_graph()
    return set(g.nodes.keys()), {(e.source, e.target) for e in g.edges}


@pytest.fixture
def user():
    return User.objects.create_user(email="i@example.com", password="x" * 12)


# ---- submission flatten -------------------------------------------------

def test_run_serializer_flattens_graph_version(user):
    v = _make_version(user)
    ser = RunCreateSerializer(
        data={"tickers": ["AAPL"], "as_of_date": "2026-01-02", "graph_version_id": v.id},
        context=_ctx(user),
    )
    assert ser.is_valid(), ser.errors
    run = ser.save(user=user)
    assert run.graph_version_id == v.id
    assert run.model_overrides["buffett"] == CUSTOM
    assert run.model_overrides["risk_manager"] and run.model_overrides["cio"]
    assert "portfolio_manager" not in run.model_overrides  # deterministic
    assert set(run.personas) == {"buffett", "munger", "graham", "wood",
                                 "druckenmiller", "burry", "damodaran", "lynch"}


def test_run_serializer_explicit_override_wins(user):
    v = _make_version(user)
    ser = RunCreateSerializer(
        data={
            "tickers": ["AAPL"], "as_of_date": "2026-01-02",
            "graph_version_id": v.id,
            "model_overrides": {"buffett": "anthropic:claude-sonnet-4-6"},
        },
        context=_ctx(user),
    )
    assert ser.is_valid(), ser.errors
    run = ser.save(user=user)
    assert run.model_overrides["buffett"] == "anthropic:claude-sonnet-4-6"


def test_run_serializer_rejects_invalid_version(user, settings):
    settings.BLOCK_INVALID_GRAPH_AT_SUBMISSION = True
    v = _make_version(user, valid=False)
    ser = RunCreateSerializer(
        data={"tickers": ["AAPL"], "as_of_date": "2026-01-02", "graph_version_id": v.id},
        context=_ctx(user),
    )
    assert not ser.is_valid()
    assert "graph_version_id" in ser.errors or "non_field_errors" in ser.errors


def test_run_serializer_rejects_unowned_version(user):
    other = User.objects.create_user(email="j@example.com", password="x" * 12)
    v = _make_version(other, name="Theirs")
    ser = RunCreateSerializer(
        data={"tickers": ["AAPL"], "as_of_date": "2026-01-02", "graph_version_id": v.id},
        context=_ctx(user),
    )
    assert not ser.is_valid()


def test_run_serializer_allows_template_version(user):
    council = AgentGraph.objects.get(name="Council classic", is_template=True)
    v = council.versions.first()
    ser = RunCreateSerializer(
        data={"tickers": ["AAPL"], "as_of_date": "2026-01-02", "graph_version_id": v.id},
        context=_ctx(user),
    )
    assert ser.is_valid(), ser.errors


def test_backtest_serializer_sets_display_label(user):
    v = _make_version(user, name="BTGraph")
    ser = BacktestCreateSerializer(
        data={
            "name": "bt1", "universe": ["AAPL"],
            "start_date": "2024-01-01", "end_date": "2025-06-01",
            "is_window_days": 252, "oos_window_days": 63,
            "graph_version_id": v.id,
        },
        context=_ctx(user),
    )
    assert ser.is_valid(), ser.errors
    bt = ser.save(user=user)
    assert bt.graph_version_id == v.id
    assert bt.agent_graph_version == v.display_label  # "BTGraph:v1"
    assert bt.model_overrides["buffett"] == CUSTOM


# ---- resolve_graph fallback / compile ----------------------------------

def test_resolve_graph_flag_off_falls_back_to_council(user, settings):
    settings.ENABLE_DB_GRAPHS = False
    v = _make_version(user, name="FlagOff")
    run = Run.objects.create(user=user, tickers=["AAPL"], as_of_date="2026-01-02",
                             personas=["buffett", "munger"], graph_version=v)
    resolved = resolve_graph(run)
    expected = build_council_graph(personas=["buffett", "munger"])
    assert _topo(resolved) == _topo(expected)


def test_resolve_graph_flag_on_compiles_version(user, settings):
    settings.ENABLE_DB_GRAPHS = True
    v = _make_version(user, name="FlagOn")
    run = Run.objects.create(user=user, tickers=["AAPL"], as_of_date="2026-01-02",
                             graph_version=v)
    assert _topo(resolve_graph(run)) == _topo(compile_graph(v))


def test_resolve_graph_no_version_uses_council(user, settings):
    settings.ENABLE_DB_GRAPHS = True
    run = Run.objects.create(user=user, tickers=["AAPL"], as_of_date="2026-01-02",
                             personas=["buffett"])
    assert _topo(resolve_graph(run)) == _topo(build_council_graph(personas=["buffett"]))


# ---- schedule propagation ----------------------------------------------

def test_flatten_helpers(user):
    v = _make_version(user, name="Flat")
    ov = model_overrides_for_version(v)
    assert ov["buffett"] == CUSTOM
    assert "portfolio_manager" not in ov
    assert set(personas_for_version(v)) == set(
        ["buffett", "munger", "graham", "wood", "druckenmiller", "burry", "damodaran", "lynch"]
    )
