"""Shared validation + healing for per-agent ``model_overrides`` dicts.

Extracted from ``RunCreateSerializer`` so the run, cycle-estimate, and
run-now endpoints all handle unknown agents / inactive models at the API
boundary using one implementation. Since P13, a dead CATALOG model no longer
rejects the request: it is healed to a live same-tier model (the self-healing
invariant — a submission must never fail because the catalog drifted under
the client's feet). Unknown agent keys and unknown ``ollama:`` ids still 400.
"""
from __future__ import annotations

import logging

from rest_framework.exceptions import ValidationError

log = logging.getLogger(__name__)


def validate_model_overrides(overrides: dict | None, user) -> dict:
    """Return ``overrides`` (healed where necessary), or raise ``ValidationError``.

    ``ollama:`` ids are validated against ``user``'s live discovery (they
    have no ``ModelEntry`` row and are intentionally per-user/ephemeral) and
    still reject when unknown — silently swapping a local model for a cloud
    one would violate the offline guarantees. Catalog ids that are inactive
    or unknown are HEALED to a live same-tier model via
    ``tier_menus.heal_overrides`` and the healed map is returned — it is what
    gets stored on the Run, so the UI and cost paths see what will actually
    execute.
    """
    if not overrides:
        return overrides or {}
    if not isinstance(overrides, dict):
        raise ValidationError("model_overrides must be an object")

    # Reject typoed agent override keys against the canonical registry so a
    # stray "fundamentls" key isn't silently ignored.
    from apps.models_catalog.presets import ALL_AGENTS

    unknown_agents = [k for k in overrides if str(k) not in ALL_AGENTS]
    if unknown_agents:
        raise ValidationError(
            f"model_overrides has unknown agent key(s): {sorted(unknown_agents)}. "
            f"Known agents: {sorted(ALL_AGENTS)}"
        )

    try:
        from django.db.models import Q

        from apps.models_catalog.models import ModelEntry, ProviderKey
        from apps.models_catalog.ollama_discovery import discover_ollama_models
    except Exception:
        return overrides

    # Split overrides by provider prefix. ollama: ids are validated against
    # the requesting user's live discovery; everything else validates against
    # the catalog.
    ollama_overrides: dict[str, str] = {}
    catalog_overrides: dict[str, str] = {}
    for agent, mid in overrides.items():
        if str(mid).startswith("ollama:"):
            ollama_overrides[agent] = str(mid)
        else:
            catalog_overrides[agent] = str(mid)

    if ollama_overrides:
        host = ""
        if user is not None and getattr(user, "is_authenticated", False):
            pk = ProviderKey.objects.filter(user=user).first()
            host = pk.ollama_host if pk else ""
        discovered = {m["id"] for m in discover_ollama_models(host)}
        for agent, mid in ollama_overrides.items():
            if mid not in discovered:
                raise ValidationError(
                    f"model_overrides[{agent!r}] = {mid!r} is not a known active model"
                )

    if catalog_overrides:
        qualified = {mid for mid in catalog_overrides.values()}
        bare = {mid.split(":", 1)[-1] for mid in qualified}
        known = set(
            ModelEntry.objects.filter(is_active=True)
            .filter(Q(id__in=qualified) | Q(id__in=bare))
            .values_list("id", flat=True)
        )
        known_short = {k.split(":", 1)[-1] for k in known}
        dead = {
            agent: mid
            for agent, mid in catalog_overrides.items()
            if mid not in known and mid.split(":", 1)[-1] not in known_short
        }
        if dead:
            # P13 self-heal: a dead/unknown catalog pick no longer 400s the
            # submission — heal it to a live same-tier model (each dead id
            # resolves its own tier via TierMembership) and store THAT. The
            # 2026-07-26 incident: the frugal preset still carried the
            # deactivated z-ai/glm-4-32b and every untouched ad-hoc analysis
            # submission was rejected.
            from apps.models_catalog.tier_menus import heal_overrides

            healed, moves = heal_overrides(dict(overrides))
            if moves:
                log.warning(
                    "validate_model_overrides: healed %d dead pick(s) at "
                    "submission: %s",
                    len(moves),
                    "; ".join(f"{m['agent']}: {m['from']} -> {m['to']}" for m in moves),
                )
            still_dead = {
                agent: mid for agent, mid in dead.items()
                if healed.get(agent) == mid
            }
            if still_dead:
                # Nothing live to heal to (empty catalog / DB down mid-request):
                # keep the old explicit rejection rather than dispatching a run
                # that is guaranteed to fail.
                agent, mid = next(iter(still_dead.items()))
                raise ValidationError(
                    f"model_overrides[{agent!r}] = {mid!r} is not a known active model"
                )
            return healed
    return overrides
