"""Curated per-preset model menus.

The expandable per-agent model selector is scoped to the active preset's
menu. dev/frugal menus are sourced from a maintained OpenRouter slug
allowlist and refreshed live by fetching.sync_tier_models(); the other
three menus are static lists of already-seeded models.

This module is imported by seed.py, so it must not import Django models.
"""
from __future__ import annotations

from decimal import Decimal

# --- dev: OpenRouter :free slugs only. sync_tier_models() drops any that
#     come back non-free (budget guard). Curated to LIVE, NON-REASONING routes:
#     reasoning slugs (gpt-oss, qwen3*) burn the budget on hidden thinking and
#     emit empty content (the qwen3.6-27b hang, runs 236/237); the removed
#     arcee-ai/trinity-large-thinking:free and minimax/minimax-m2.5:free 404'd
#     upstream (runs 228-235). The free non-reasoning pool is small, so the
#     dev preset spreads its 8 personas across these (some reused) rather than
#     1-per-persona. test_preset_invariants enforces non-reasoning + vetted.
DEV_TIER_SLUGS: list[str] = [
    "nvidia/nemotron-3-super-120b-a12b:free",
    "deepseek/deepseek-v4-flash:free",
    "google/gemma-4-31b-it:free",
    "z-ai/glm-4.5-air:free",
]

# --- frugal: cheap OpenRouter PAID slugs (stable, non-ephemeral). All
#     NON-REASONING — the reasoning routes (openai/gpt-oss-120b,
#     qwen/qwen3-235b-a22b-2507, qwen/qwen3.6-27b) were removed because they
#     reasoning-exhaust to empty content on the cheap tier. amazon/nova-lite-v1
#     was dropped after a validation run degraded 4 of its council agents
#     (it returns non-schema-conforming output, not empty content, so it can't
#     even self-heal-hop). sync_tier_models() drops any above the
#     FRUGAL_PRICE_CEILING_* bounds.
FRUGAL_TIER_SLUGS: list[str] = [
    "meta-llama/llama-3.3-70b-instruct",
    "nvidia/nemotron-3-nano-30b-a3b",
    "mistralai/mistral-small-3.2-24b-instruct",
    "google/gemma-3-27b-it",
    "z-ai/glm-4-32b",
    "deepseek/deepseek-v4-flash",
]

# --- reasoning models — a curated OpenRouter allowlist of dedicated
#     chain-of-thought routes (per openrouter._REASONING_SLUGS). Offered in the
#     research/quality menus only, for users who want reasoning depth on the
#     ANALYTICAL roles. They are NEVER wired as a decision-role default: the
#     personas/PM/RM/CIO path stays Anthropic-anchored because reasoning models
#     can reasoning-exhaust to empty content (the qwen3.6-27b hang, runs 236/237
#     — see test_preset_invariants). Unlike DEV/FRUGAL these are NOT refreshed by
#     sync_tier_models(); they're statically priced in seed.py and kept fresh by
#     `verify_openrouter_pricing` (verify_models audits every active OpenRouter
#     row), which is what the §9.15 freshness invariant requires.
REASONING_TIER_SLUGS: list[str] = [
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",  # free reasoning
    "deepseek/deepseek-v4-pro",                            # flagship reasoning
    "z-ai/glm-5.1",                                        # mid reasoning
]

_REASONING_MENU_IDS = [f"openrouter:{s}" for s in REASONING_TIER_SLUGS]

# --- static menus for the Anthropic-anchored tiers (decision #4). These
#     reference existing ModelEntry ids; not fetched. Any "openrouter:" id
#     listed here MUST be in a curated allowlist whose pricing stays fresh —
#     DEV/FRUGAL_TIER_SLUGS (live sync) or REASONING_TIER_SLUGS (verify_models)
#     — see the static-menu invariant in §9.15.
STATIC_TIER_MENUS: dict[str, list[str]] = {
    "research": [
        "anthropic:claude-sonnet-4-6",
        "anthropic:claude-haiku-4-5-20251001",
        "anthropic:claude-opus-4-7",
        *_REASONING_MENU_IDS,
    ],
    "quality": [
        "anthropic:claude-opus-4-7",
        "anthropic:claude-sonnet-4-6",
        "anthropic:claude-haiku-4-5-20251001",
        *_REASONING_MENU_IDS,
    ],
    "hybrid": [
        "anthropic:claude-sonnet-4-6",
        "anthropic:claude-haiku-4-5-20251001",
        "openrouter:meta-llama/llama-3.3-70b-instruct",
    ],
}

# frugal price guard — USD per Mtok. A slug whose live pricing exceeds
# either bound is excluded from the menu and flagged by the sync. Sits
# above the priciest legitimately-curated frugal slug (qwen/qwen3.6-27b
# at ~0.30 in / ~3.20 out per seed.py) while still catching a jump toward
# frontier pricing (Sonnet 3/15, Opus 15/75). If you allowlist a pricier
# model, raise these — test §9.13 guards it.
FRUGAL_PRICE_CEILING_IN = Decimal("1.00")
FRUGAL_PRICE_CEILING_OUT = Decimal("5.00")


def _default_menu(preset: str) -> list[str]:
    """The baseline menu from the DEFAULT_* constants — used as the cold-start
    fallback when a tier has no DB membership yet (or the DB is unreachable
    pre-migrate). dev/frugal resolve to "openrouter:"-prefixed allowlist ids; the
    others return their static list. Unknown preset -> []."""
    if preset == "dev":
        return [f"openrouter:{s}" for s in DEV_TIER_SLUGS]
    if preset == "frugal":
        return [f"openrouter:{s}" for s in FRUGAL_TIER_SLUGS]
    return list(STATIC_TIER_MENUS.get(preset, []))


def tier_menu(preset: str) -> list[str]:
    """Ordered ModelEntry ids for a preset's menu.

    Reads the DB `TierMembership` for the tier, filtered to ACTIVE models — so a
    deactivated/delisted model auto-drops from every menu (the core resilience
    behavior). Falls back to the baseline constants only when the tier is
    UNSEEDED (no membership rows) or the DB is unavailable. A seeded tier whose
    members are all inactive returns [] (honest), not the stale baseline.
    Unknown preset -> [].
    """
    try:
        from .models import TierMembership
        rows = list(
            TierMembership.objects.filter(tier_id=preset)
            .order_by("ordering", "model_id")
            .values_list("model_id", "model__is_active")
        )
    except Exception:
        return _default_menu(preset)
    if not rows:
        return _default_menu(preset)
    return [mid for mid, active in rows if active]


def tier_default(preset: str) -> str | None:
    """The tier's default model id — the resilience fallback target.

    `TierConfig.default_model` when it's still active, else the first active menu
    member, else None.
    """
    try:
        from .models import TierConfig
        tc = (
            TierConfig.objects.filter(tier_name=preset)
            .select_related("default_model")
            .first()
        )
    except Exception:
        tc = None
    if tc and tc.default_model_id and tc.default_model and tc.default_model.is_active:
        return tc.default_model_id
    menu = tier_menu(preset)
    return menu[0] if menu else None


def sanitize_overrides(
    preset: str | None,
    overrides: dict[str, str],
    *,
    fallback: str | None = None,
) -> dict[str, str]:
    """Rewrite any override pointing at an INACTIVE catalog model to the tier's
    default (or `fallback`), leaving active picks untouched.

    This degrades a delisted/deactivated curated slug to a live model at
    SELECTION time — complementing the runtime self-heal — WITHOUT collapsing the
    persona spread: only entries whose model is inactive move, every still-active
    persona keeps its distinct model. `ollama:` ids are exempt (no ModelEntry row
    by design). If nothing live can be found to fall back to, the map is returned
    unchanged (the runtime L1-L3 self-heal remains the last net).
    """
    if not overrides:
        return overrides
    catalog_ids = {
        str(m) for m in overrides.values() if not str(m).startswith("ollama:")
    }
    if not catalog_ids:
        return overrides
    # Check the fallback's activity in the SAME query so we never rewrite a dead
    # pick to an equally-dead user/operator default.
    check_ids = set(catalog_ids)
    if fallback and not str(fallback).startswith("ollama:"):
        check_ids.add(str(fallback))
    try:
        from .models import ModelEntry
        active = set(
            ModelEntry.objects.filter(id__in=check_ids, is_active=True)
            .values_list("id", flat=True)
        )
    except Exception:
        return overrides
    dead = catalog_ids - active
    if not dead:
        return overrides
    # Honor an explicit fallback only if it is itself active; otherwise fall
    # through to the tier default (which tier_default guarantees is active).
    target = fallback if (fallback and fallback in active) else tier_default(preset or "")
    if not target or target in dead:
        return overrides
    return {
        agent: (target if str(mid) in dead else mid)
        for agent, mid in overrides.items()
    }


def anchor_non_personas(
    preset: str | None,
    overrides: dict[str, str],
    tier_choice: str | None,
) -> dict[str, str]:
    """Set every NON-persona role to `tier_choice` (the per-tier default),
    leaving the personas' spread intact. No-op for 'hybrid' (whose non-persona
    roles are intentionally local/Sonnet) or when `tier_choice` is falsy."""
    if not tier_choice or preset == "hybrid":
        return overrides
    from .presets import PERSONA_AGENTS
    return {
        agent: (mid if agent in PERSONA_AGENTS else tier_choice)
        for agent, mid in overrides.items()
    }
