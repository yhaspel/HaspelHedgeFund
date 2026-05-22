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


@pytest.mark.django_db
def test_run_records_council_graph_version_and_spec_hashes_p02a() -> None:
    """P02a review regression: every Run that finishes the council path must
    record (a) the council graph version under ``__graph__`` and (b) a
    ``<version>:<spec_hash>`` value per registered agent so backtests can
    replay against the exact prompt+model+config+code tuple that produced
    them.

    Test exercises ``Run.save()`` + ``snapshot_versions`` end-to-end
    without invoking the LLM. The set of registered agents is the source
    of truth — agents not yet pinned by ``register(AgentSpec(...))`` are
    intentionally absent from the snapshot.
    """
    from django.contrib.auth import get_user_model

    from apps.runs.models import Run
    from hedgefund_agents.graphs.council import ANALYTICAL_NODES
    from hedgefund_agents.personas import ALL_PERSONAS
    from hedgefund_agents.versioning import COUNCIL_GRAPH_VERSION

    User = get_user_model()
    user = User.objects.create_user(email="v@v.com", password="x" * 12)
    ensure_versions_synced()
    selected = list(ALL_PERSONAS)
    snap = snapshot_versions(
        list(ANALYTICAL_NODES.keys())
        + selected
        + ["risk_manager", "portfolio_manager", "cio"]
    )
    run = Run.objects.create(user=user, tickers=["AAPL"], as_of_date="2024-12-31",
                             agent_versions=snap)
    # 1) Graph composition version is always present.
    assert run.agent_versions["__graph__"] == COUNCIL_GRAPH_VERSION
    # 2) Every persona is recorded as "<version>:<spec_hash>".
    for persona in selected:
        val = run.agent_versions[persona]
        assert ":" in val, f"{persona} value should be '<version>:<spec_hash>': {val!r}"
        version_part, hash_part = val.split(":", 1)
        assert version_part, f"empty version for {persona}"
        assert len(hash_part) == 16, f"spec_hash for {persona} not a 16-hex prefix"
    # 3) The synthesis-layer agents that have AgentSpecs must be persisted.
    for agent in ("risk_manager", "portfolio_manager", "cio", "macro", "news_digest"):
        assert agent in run.agent_versions, f"{agent} missing from snapshot"
        assert ":" in run.agent_versions[agent], (
            f"{agent} entry missing spec_hash: {run.agent_versions[agent]!r}"
        )
