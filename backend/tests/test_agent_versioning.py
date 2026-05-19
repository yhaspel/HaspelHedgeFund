"""AgentVersion fingerprint sensitivity.

spec_hash must change when any of: prompt, default_model, config, or
code_version changes. snapshot_versions must include the council graph
composition version under __graph__.
"""
from __future__ import annotations

from hedgefund_agents.versioning import (
    AGENT_VERSIONS,
    COUNCIL_GRAPH_VERSION,
    AgentSpec,
    register,
    snapshot_versions,
)


def _base() -> AgentSpec:
    return AgentSpec(
        agent_name="_vtest",
        version="v1",
        default_model="anthropic:claude-haiku-4-5-20251001",
        prompt="hello world",
        config={"x": 1},
        code_version="v1",
    )


def test_spec_hash_changes_on_prompt_change() -> None:
    a = _base()
    b = AgentSpec(**{**a.__dict__, "prompt": "hello WORLD"})
    assert a.spec_hash != b.spec_hash


def test_spec_hash_changes_on_default_model_change() -> None:
    a = _base()
    b = AgentSpec(**{**a.__dict__, "default_model": "openrouter:qwen/qwen3.6-27b"})
    assert a.spec_hash != b.spec_hash


def test_spec_hash_changes_on_config_change() -> None:
    a = _base()
    b = AgentSpec(**{**a.__dict__, "config": {"x": 2}})
    assert a.spec_hash != b.spec_hash


def test_spec_hash_changes_on_code_version_change() -> None:
    a = _base()
    b = AgentSpec(**{**a.__dict__, "code_version": "v2"})
    assert a.spec_hash != b.spec_hash


def test_spec_hash_stable_for_equal_specs() -> None:
    assert _base().spec_hash == _base().spec_hash


def test_snapshot_includes_graph_version_and_spec_hash() -> None:
    spec = _base()
    AGENT_VERSIONS.pop(spec.agent_name, None)
    try:
        register(spec)
        snap = snapshot_versions([spec.agent_name])
        assert snap["__graph__"] == COUNCIL_GRAPH_VERSION
        assert snap[spec.agent_name] == f"{spec.version}:{spec.spec_hash}"
    finally:
        AGENT_VERSIONS.pop(spec.agent_name, None)
