"""Persona-evolution data models (P3-D WS-A).

Three concepts:

* ``PersonaEvolutionProfile`` — one row per persona. Global / shared. Holds the
  living-investor metadata (display name, firm, search aliases) and a denormalised
  pointer to the latest revision.
* ``PersonaEvolutionRevision`` — append-only history of distilled dossiers
  produced by the evolution engine. The text injected at run time is
  ``composite_markdown()``.
* ``PersonaEvolutionSettings`` — per-user cadence / model / cost-cap settings.
  In single-user scope this is effectively app-level (decision 8).

Nothing in this app is allowed inside the backtest path. The PIT regression
test in ``backend/tests/test_persona_evolution.py`` asserts the boundary.
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models


class PersonaEvolutionProfile(models.Model):
    """One row per persona. Global — shared by all users (decision 8)."""

    NEVER = "never"
    OK = "ok"
    SKIPPED = "skipped"
    FAILED = "failed"
    STATUS_CHOICES = [
        (NEVER, "Never"),
        (OK, "Ok"),
        (SKIPPED, "Skipped"),
        (FAILED, "Failed"),
    ]

    persona_name = models.CharField(max_length=32, unique=True)
    display_name = models.CharField(max_length=64)
    firm_name = models.CharField(max_length=128, blank=True, default="")
    search_aliases = models.JSONField(default=list, blank=True)
    is_evolvable = models.BooleanField(default=True)
    lifecycle_note = models.CharField(max_length=256, blank=True, default="")

    current_revision = models.ForeignKey(
        "PersonaEvolutionRevision",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    last_cycle_at = models.DateTimeField(null=True, blank=True)
    last_cycle_status = models.CharField(
        max_length=12, choices=STATUS_CHOICES, default=NEVER
    )
    last_cycle_note = models.TextField(blank=True, default="")
    # Set when a cycle is in-flight for this persona; cleared when the cycle
    # finishes (success or failure). The frontend polls profiles and shows
    # a "running" badge for any profile with this non-null. Survives a page
    # reload — the running state lives in the DB, not in the browser tab.
    current_cycle_started_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["persona_name"]

    def __str__(self) -> str:
        return f"PersonaEvolutionProfile({self.persona_name})"


class PersonaEvolutionRevision(models.Model):
    """Append-only. One row per successful evolution cycle for a persona."""

    profile = models.ForeignKey(
        PersonaEvolutionProfile,
        on_delete=models.CASCADE,
        related_name="revisions",
    )
    seq = models.PositiveIntegerField()
    as_of_date = models.DateField()

    market_stance_md = models.TextField(blank=True, default="")
    general_notes_md = models.TextField(blank=True, default="")
    char_count = models.PositiveIntegerField(default=0)
    over_budget = models.BooleanField(default=False)

    material_change = models.BooleanField(default=True)
    dropped_facts = models.JSONField(default=list, blank=True)
    source_urls = models.JSONField(default=list, blank=True)
    raw_inputs = models.JSONField(default=dict, blank=True)

    model_id = models.CharField(max_length=128, blank=True, default="")
    llm_call = models.ForeignKey(
        "hedgefund_agents.LLMCall",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-seq"]
        unique_together = ("profile", "seq")
        indexes = [models.Index(fields=["profile", "-as_of_date"])]

    def __str__(self) -> str:
        return f"PersonaEvolutionRevision(persona={self.profile.persona_name}, seq={self.seq})"

    def composite_markdown(self) -> str:
        """The exact text injected at run time (see persona_evolution_block).

        Fixed shape so the backtest-boundary test can pattern-match it.
        Empty sections are omitted. If both are empty the composite is ``""``
        (no revision should ever have been written in that case — §8.6 — but
        be defensive about callers anyway).
        """
        stance = (self.market_stance_md or "").strip()
        notes = (self.general_notes_md or "").strip()
        parts: list[str] = []
        if stance:
            parts.append(
                f"RECENT MARKET STANCE & MOVES (as of {self.as_of_date.isoformat()})\n{stance}"
            )
        if notes:
            parts.append(f"GENERAL NOTES\n{notes}")
        return "\n\n".join(parts)


class PersonaEvolutionSettings(models.Model):
    """Per-user, but effectively app-level in single-user scope (decision 8)."""

    OFF = "off"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    CADENCE_CHOICES = [
        (OFF, "Off"),
        (DAILY, "Daily"),
        (WEEKLY, "Weekly"),
        (MONTHLY, "Monthly"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="persona_evolution_settings",
    )
    enabled = models.BooleanField(default=False)
    cadence = models.CharField(max_length=8, choices=CADENCE_CHOICES, default=OFF)
    model_id = models.CharField(max_length=128, blank=True, default="")
    web_search_enabled = models.BooleanField(default=True)
    monthly_cost_cap_usd = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal("2.00")
    )
    cost_cap_reached_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return (
            f"PersonaEvolutionSettings(user={self.user_id}, "
            f"enabled={self.enabled}, cadence={self.cadence})"
        )


CADENCE_INTERVAL_DAYS = {
    PersonaEvolutionSettings.DAILY: 1,
    PersonaEvolutionSettings.WEEKLY: 7,
    PersonaEvolutionSettings.MONTHLY: 30,
}

PERSONA_EVOLUTION_AGENT_NAME = "persona_evolution"
