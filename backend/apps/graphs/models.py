"""User-composed agent graphs (P4c).

An ``AgentGraph`` is a named, user-owned (or platform-template) container. Each
save produces an immutable ``AgentGraphVersion`` holding the canvas JSON. Runs /
backtests / scheduled-runs reference the exact version they executed via an FK
with ``on_delete=PROTECT``, so a 30-day-old backtest can never lose its graph.

Nodes/edges are stored as JSON on the version (not normalized rows) to keep the
schema migration-light and the export format portable (D4 / Risk 7).
"""
from __future__ import annotations

from django.conf import settings
from django.db import models


class AgentGraph(models.Model):
    # Platform starter templates have user=NULL, is_template=True and live in a
    # separate namespace from users' own graphs (D4). The user-visible queryset
    # is Q(user=request.user) | Q(is_template=True).
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        related_name="agent_graphs",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    is_template = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            # Names are unique per user. Templates (user=NULL) are exempt in
            # Postgres (NULLs are distinct) — they are platform-curated.
            models.UniqueConstraint(fields=["user", "name"], name="uniq_graph_name_per_user"),
        ]

    def __str__(self) -> str:
        owner = "template" if self.is_template else f"user={self.user_id}"
        return f"AgentGraph #{self.pk} {self.name!r} ({owner})"

    def next_version_number(self) -> int:
        latest = self.versions.order_by("-version").first()
        return (latest.version + 1) if latest else 1


class AgentGraphVersion(models.Model):
    VALID = "valid"
    DRAFT_INVALID = "draft_invalid"

    graph = models.ForeignKey(AgentGraph, related_name="versions", on_delete=models.CASCADE)
    version = models.IntegerField()  # auto-increment per graph
    # USER-editable nodes only (analytical tier + personas). The structural
    # joins and the risk/PM/CIO tail are NEVER stored here — the compiler
    # injects them. See registry.SCHEMA_VERSION / the JSON schema in the plan.
    nodes = models.JSONField(default=list)
    edges = models.JSONField(default=list)
    # Models for the two LLM tail nodes: {"risk_manager": "provider:model",
    # "cio": "provider:model"}. PM is deterministic and absent.
    tail_models = models.JSONField(default=dict)
    validation_status = models.CharField(max_length=16, default=DRAFT_INVALID)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["graph_id", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["graph", "version"], name="uniq_version_per_graph"),
        ]

    def __str__(self) -> str:
        return f"{self.graph.name}:v{self.version} ({self.validation_status})"

    @property
    def display_label(self) -> str:
        """Denormalized label stored on Backtest.agent_graph_version, e.g.
        "my-graph:v3"."""
        return f"{self.graph.name}:v{self.version}"
