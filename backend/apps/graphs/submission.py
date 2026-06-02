"""Submission-time helpers shared by the Run / Backtest / ScheduledRun
serializers and the schedule dispatcher (P4c).

When a graph version is selected for a run, its per-node models flatten into
`model_overrides` (the keys are the `pick_model` agent_names) and its persona
nodes become the `personas` subset — so both the compiled-graph path and the
council fallback honor the user's choice.
"""
from __future__ import annotations

from django.conf import settings
from rest_framework import serializers

from .registry import ALL_AGENTS


def model_overrides_for_version(version) -> dict:
    """Flatten a version's node models + tail_models into a model_overrides dict
    keyed by agent_name (what pick_model consumes)."""
    out: dict[str, str] = {}
    for node in version.nodes or []:
        spec = ALL_AGENTS.get(node.get("type"))
        if spec and spec.model_selectable and node.get("model_id"):
            out[spec.agent_name] = node["model_id"]
    for key, val in (version.tail_models or {}).items():
        if val:
            out[key] = val
    return out


def personas_for_version(version) -> list[str]:
    """The persona node types present in a version (the persona subset)."""
    return [
        node["type"]
        for node in (version.nodes or [])
        if ALL_AGENTS.get(node.get("type")) and ALL_AGENTS[node["type"]].kind == "persona"
    ]


def check_submittable(version, user):
    """Validate that `user` may run `version` (raises DRF ValidationError).

    Accessible iff the version's graph is a template or owned by the user; and,
    when BLOCK_INVALID_GRAPH_AT_SUBMISSION is on, the version must be valid.
    """
    graph = version.graph
    if not (graph.is_template or graph.user_id == getattr(user, "id", None)):
        raise serializers.ValidationError("graph_version is not accessible to you.")
    if (getattr(settings, "BLOCK_INVALID_GRAPH_AT_SUBMISSION", True)
            and version.validation_status != version.VALID):
        raise serializers.ValidationError(
            "The selected graph version is not valid; fix the graph before running it."
        )
    return version


def apply_graph_version(attrs: dict, version, *, user) -> dict:
    """Mutate `attrs` so a submitted Run/Backtest honors the chosen version:
    flatten models into model_overrides (user-provided overrides win on
    conflict) and set the persona subset. Call after check_submittable."""
    flattened = model_overrides_for_version(version)
    explicit = attrs.get("model_overrides") or {}
    merged = {**flattened, **explicit}  # explicit user overrides take precedence
    attrs["model_overrides"] = merged
    personas = personas_for_version(version)
    if personas:
        attrs["personas"] = personas
    return attrs
