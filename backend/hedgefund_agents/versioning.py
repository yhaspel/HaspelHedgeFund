"""Agent versioning.

Every agent's (prompt, default model, tools/config) tuple is pinned to a
named version. Runs snapshot which version of each agent executed, so
later analysis can correlate decisions to the exact prompt+model that
produced them.

The source of truth is the in-process `AGENT_VERSIONS` dict; the
`AgentVersion` Django model mirrors it for queryability/auditing.
`ensure_versions_synced()` upserts the registry to the DB on demand.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from django.db import models


@dataclass(frozen=True)
class AgentSpec:
    agent_name: str
    version: str
    default_model: str  # "provider:model"
    prompt: str
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def prompt_hash(self) -> str:
        return hashlib.sha256(self.prompt.encode()).hexdigest()[:16]


# Source of truth: edit prompts here, bump version when prompt changes.
AGENT_VERSIONS: dict[str, AgentSpec] = {}


def register(spec: AgentSpec) -> AgentSpec:
    AGENT_VERSIONS[spec.agent_name] = spec
    return spec


def snapshot_versions(agent_names: list[str]) -> dict[str, str]:
    """Used by Run.tasks to record which agent versions executed."""
    return {n: AGENT_VERSIONS[n].version for n in agent_names if n in AGENT_VERSIONS}


class AgentVersion(models.Model):
    """DB mirror of AGENT_VERSIONS for audit/queries."""
    agent_name = models.CharField(max_length=64)
    version = models.CharField(max_length=32)
    prompt_hash = models.CharField(max_length=64)
    default_model = models.CharField(max_length=128)
    config = models.JSONField(default=dict, blank=True)
    is_current = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "hedgefund_agents"
        unique_together = ("agent_name", "version")

    def __str__(self) -> str:
        return f"{self.agent_name}@{self.version}"


def ensure_versions_synced() -> None:
    """Upsert the in-process registry into AgentVersion. Idempotent.

    Marks the registered spec as is_current=True and any older version of
    the same agent as is_current=False.
    """
    for spec in AGENT_VERSIONS.values():
        AgentVersion.objects.update_or_create(
            agent_name=spec.agent_name,
            version=spec.version,
            defaults={
                "prompt_hash": spec.prompt_hash,
                "default_model": spec.default_model,
                "config": spec.config,
                "is_current": True,
            },
        )
        AgentVersion.objects.filter(agent_name=spec.agent_name).exclude(
            version=spec.version
        ).update(is_current=False)
