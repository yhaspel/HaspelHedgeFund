"""Shared validation for per-agent ``model_overrides`` dicts.

Extracted from ``RunCreateSerializer`` so the run, cycle-estimate, and
run-now endpoints all reject unknown agents / inactive models / unreachable
providers at the API boundary using one implementation.
"""
from __future__ import annotations

from rest_framework.exceptions import ValidationError


def validate_model_overrides(overrides: dict | None, user) -> dict:
    """Return ``overrides`` unchanged, or raise ``ValidationError``.

    ``ollama:`` ids are validated against ``user``'s live discovery (they
    have no ``ModelEntry`` row and are intentionally per-user/ephemeral);
    everything else validates against the active catalog. Mirrors the
    precedence used at dispatch.
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
        for agent, mid in catalog_overrides.items():
            short = mid.split(":", 1)[-1]
            if mid not in known and short not in known_short:
                raise ValidationError(
                    f"model_overrides[{agent!r}] = {mid!r} is not a known active model"
                )
    return overrides
