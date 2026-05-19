"""AgentVersion registry tests."""
from __future__ import annotations

import pytest

import hedgefund_agents.personas  # noqa: F401 — register specs
from hedgefund_agents.versioning import (
    AGENT_VERSIONS,
    AgentSpec,
    ensure_versions_synced,
    register,
    snapshot_versions,
)

_EXPECTED_PERSONAS = (
    "buffett", "munger", "graham", "wood",
    "druckenmiller", "burry", "damodaran", "lynch",
)


def test_all_personas_registered() -> None:
    for name in _EXPECTED_PERSONAS:
        assert name in AGENT_VERSIONS, f"{name} missing from registry"
        assert AGENT_VERSIONS[name].config.get("kind") == "persona"


def test_prompt_hash_is_stable() -> None:
    spec = AGENT_VERSIONS["buffett"]
    assert len(spec.prompt_hash) == 16
    assert spec.prompt_hash == AgentSpec(
        agent_name="buffett",
        version="vX",
        default_model="x:y",
        prompt=spec.prompt,
    ).prompt_hash


def test_snapshot_versions_records_executed_agents() -> None:
    snap = snapshot_versions(["buffett", "munger", "nonexistent"])
    buf = AGENT_VERSIONS["buffett"]
    mun = AGENT_VERSIONS["munger"]
    # Format: "<version>:<spec_hash>" — full fingerprint travels with the run.
    assert snap["buffett"] == f"{buf.version}:{buf.spec_hash}"
    assert snap["munger"] == f"{mun.version}:{mun.spec_hash}"
    assert "nonexistent" not in snap
    assert "__graph__" in snap


@pytest.mark.django_db
def test_ensure_versions_synced_creates_db_rows() -> None:
    from hedgefund_agents.versioning import AgentVersion
    ensure_versions_synced()
    assert AgentVersion.objects.filter(agent_name="buffett", is_current=True).exists()


@pytest.mark.django_db
def test_bumping_version_marks_old_not_current() -> None:
    from hedgefund_agents.versioning import AgentVersion
    ensure_versions_synced()
    # Save current spec, bump version.
    original = AGENT_VERSIONS["buffett"]
    try:
        register(AgentSpec(
            agent_name="buffett",
            version="v99",
            default_model=original.default_model,
            prompt=original.prompt + "\n# bump",
            config=original.config,
        ))
        ensure_versions_synced()
        assert AgentVersion.objects.get(agent_name="buffett", version="v99").is_current
        prior = AgentVersion.objects.get(agent_name="buffett", version=original.version)
        assert not prior.is_current
    finally:
        register(original)  # restore for other tests
