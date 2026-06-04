"""CI invariants for the model PRESETS (apps/models_catalog/presets.py).

Two production incidents motivate these:
  - runs 228-235: the `dev` preset referenced OpenRouter :free slugs that had
    been removed upstream (404) — a slug that isn't a curated/vetted model.
  - runs 236/237: the `frugal` preset routed personas/decision roles to the
    reasoning slug qwen/qwen3.6-27b, which burns its budget on hidden thinking,
    returns empty content, and (pre self-heal) hung the worker.

These are now caught at the source. The self-heal layer is the runtime safety
net; this test is the source-of-truth guard so a bad slug can't be re-introduced
into a preset without CI going red.
"""
from __future__ import annotations

import pytest

from apps.models_catalog.presets import PERSONA_AGENTS, PRESETS, expand_preset
from apps.models_catalog.tier_menus import (
    DEV_TIER_SLUGS,
    FRUGAL_TIER_SLUGS,
    STATIC_TIER_MENUS,
)
from hedgefund_agents.llm.adapters.openrouter import _REASONING_SLUGS

# Roles where a reasoning model's empty-content failure mode hurts most: the
# personas that vote and the risk/PM/CIO synthesis that decides.
DECISION_ROLES = PERSONA_AGENTS | {"risk_manager", "portfolio_manager", "cio"}


def _vetted_ids() -> set[str]:
    """The curated, vetted model id set: the dev/frugal allowlists (kept fresh
    against OpenRouter by sync_tier_models) plus the static Anthropic-anchored
    menus. A preset must never reference a slug outside this set."""
    ids = {f"openrouter:{s}" for s in (*DEV_TIER_SLUGS, *FRUGAL_TIER_SLUGS)}
    for menu in STATIC_TIER_MENUS.values():
        ids.update(menu)
    return ids


def _is_reasoning(model_id: str) -> bool:
    return any(tag in model_id.lower() for tag in _REASONING_SLUGS)


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_every_preset_model_is_vetted(preset: str) -> None:
    """Every model a preset resolves to (wildcards expanded, `<local-tier-a>`
    resolved) must be in a curated tier menu — catches typos and un-vetted /
    upstream-removed slugs (the arcee/minimax 404 class)."""
    vetted = _vetted_ids()
    for agent, model_id in expand_preset(preset).items():
        assert model_id in vetted, (
            f"preset {preset!r} maps {agent!r} -> {model_id!r}, which is not in "
            f"any curated tier menu (DEV/FRUGAL allowlist or STATIC_TIER_MENUS). "
            f"Add it to a tier menu or use a vetted slug."
        )


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_no_reasoning_model_on_a_decision_role(preset: str) -> None:
    """No preset may put a reasoning slug (per the adapter's _REASONING_SLUGS)
    on a decision role — they reasoning-exhaust to empty content (qwen3.6-27b,
    runs 236/237)."""
    for agent, model_id in expand_preset(preset).items():
        if agent in DECISION_ROLES:
            assert not _is_reasoning(model_id), (
                f"preset {preset!r} puts reasoning model {model_id!r} on decision "
                f"role {agent!r}; use a non-reasoning slug."
            )


def test_cheap_tier_menus_have_no_reasoning_slugs() -> None:
    """The dev/frugal menus feed the cheap-tier decision roles, so the menus
    themselves must stay reasoning-free — prevents re-adding qwen3.6-27b etc."""
    for slug in (*DEV_TIER_SLUGS, *FRUGAL_TIER_SLUGS):
        assert not _is_reasoning(slug), (
            f"{slug!r} is a reasoning slug; it empties out on the cheap tiers "
            f"(qwen3.6-27b hang). Keep DEV/FRUGAL menus non-reasoning."
        )


def test_dev_and_frugal_menus_are_disjoint() -> None:
    """Dev (:free) and frugal (paid) menus must not share a slug — the model
    catalog sync builds rows from both lists with different free/paid pricing."""
    overlap = set(DEV_TIER_SLUGS) & set(FRUGAL_TIER_SLUGS)
    assert not overlap, f"dev and frugal menus overlap on: {sorted(overlap)}"


def test_blocked_fallback_is_free_vetted_and_non_reasoning() -> None:
    """registry._BLOCKED_FALLBACK is the universal model every agent uses when
    BLOCK_ANTHROPIC is on and an agent has no override. It bypasses the presets,
    so it gets the SAME guards: a free OpenRouter slug, drawn from the vetted dev
    menu, and NON-reasoning (the old GPT-OSS 120B reasoning pick emptied out)."""
    from hedgefund_agents.registry import _BLOCKED_FALLBACK

    provider, model = _BLOCKED_FALLBACK
    assert provider == "openrouter"
    assert model.endswith(":free"), model
    assert not _is_reasoning(model), f"{model} is a reasoning slug"
    assert model in DEV_TIER_SLUGS, f"{model} should be a vetted dev-menu slug"
