"""Celery task driving the persona-evolution beat (P3-D WS-C).

The beat fires daily; per-persona cadence is decided inside the task by
``is_cycle_due`` so one static schedule serves daily/weekly/monthly.
"""
from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.contrib.auth import get_user_model
from django.utils import timezone

from .engine import (
    cost_cap_reached,
    is_cycle_due,
    run_cycle_for_persona,
)
from .models import PersonaEvolutionProfile, PersonaEvolutionSettings

log = logging.getLogger(__name__)


def _settings_for_cadence_user() -> PersonaEvolutionSettings | None:
    """Return the operating user's settings row, if any. In single-user
    scope (decision 8) this is whichever user enabled the feature; if more
    than one user has settings rows we pick the most recently updated
    enabled one. Multi-user cadence arbitration is P4a."""
    qs = PersonaEvolutionSettings.objects.filter(enabled=True).order_by("-updated_at")
    return qs.first()


def _resolve_target_user(user_id: int | None) -> PersonaEvolutionSettings | None:
    if user_id is None:
        return _settings_for_cadence_user()
    User = get_user_model()
    user = User.objects.filter(pk=user_id).first()
    if user is None:
        return None
    obj, _ = PersonaEvolutionSettings.objects.get_or_create(user=user)
    return obj


@shared_task
def evolve_personas(
    user_id: int | None = None,
    persona: str | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Iterate evolvable profiles, run cycles for those whose cadence is due.

    ``force=True`` (used by the management command and the "Run now" button)
    bypasses ``is_cycle_due`` but still respects ``is_evolvable`` and the
    monthly cost cap.
    """
    user_settings = _resolve_target_user(user_id)
    if user_settings is None:
        return {"ran": [], "skipped": [], "failed": [], "reason": "no_enabled_user"}
    if not force and not user_settings.enabled:
        return {"ran": [], "skipped": [], "failed": [], "reason": "disabled"}

    if cost_cap_reached(user_settings):
        if user_settings.cost_cap_reached_at is None:
            user_settings.cost_cap_reached_at = timezone.now()
            user_settings.save(update_fields=["cost_cap_reached_at", "updated_at"])
        return {
            "ran": [],
            "skipped": [],
            "failed": [],
            "reason": "monthly_cost_cap_reached",
        }
    # Clear the cap marker if a new month rolled it back.
    if user_settings.cost_cap_reached_at is not None:
        user_settings.cost_cap_reached_at = None
        user_settings.save(update_fields=["cost_cap_reached_at", "updated_at"])

    qs = PersonaEvolutionProfile.objects.filter(is_evolvable=True)
    if persona:
        qs = qs.filter(persona_name=persona)

    ran: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    now = timezone.now()

    for profile in qs:
        if not force and not is_cycle_due(profile, user_settings, now=now):
            skipped.append(profile.persona_name)
            continue
        try:
            result = run_cycle_for_persona(profile, user_settings, today=now.date())
        except Exception:  # noqa: BLE001 — one persona failing must not abort the rest
            log.exception(
                "persona_evolution: unexpected error in cycle for %s", profile.persona_name
            )
            failed.append(profile.persona_name)
            continue
        if result.status == PersonaEvolutionProfile.OK:
            ran.append(profile.persona_name)
        elif result.status == PersonaEvolutionProfile.FAILED:
            failed.append(profile.persona_name)
        else:
            skipped.append(profile.persona_name)

        # Cost-cap re-check between personas so we don't barrel past the cap.
        if cost_cap_reached(user_settings):
            user_settings.cost_cap_reached_at = timezone.now()
            user_settings.save(update_fields=["cost_cap_reached_at", "updated_at"])
            break

    return {"ran": ran, "skipped": skipped, "failed": failed}
