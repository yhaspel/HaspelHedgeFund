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
#     reasoning-exhaust to empty content on the cheap tier. sync_tier_models()
#     drops any above the FRUGAL_PRICE_CEILING_* bounds.
FRUGAL_TIER_SLUGS: list[str] = [
    "meta-llama/llama-3.3-70b-instruct",
    "nvidia/nemotron-3-nano-30b-a3b",
    "mistralai/mistral-small-3.2-24b-instruct",
    "google/gemma-3-27b-it",
    "z-ai/glm-4-32b",
    "deepseek/deepseek-v4-flash",
    "amazon/nova-lite-v1",
]

# --- static menus for the Anthropic-anchored tiers (decision #4). These
#     reference existing ModelEntry ids; not fetched. Any "openrouter:" id
#     listed here MUST also be in DEV/FRUGAL_TIER_SLUGS so the sync keeps
#     its pricing fresh (see the static-menu invariant in §9.15).
STATIC_TIER_MENUS: dict[str, list[str]] = {
    "research": [
        "anthropic:claude-sonnet-4-6",
        "anthropic:claude-haiku-4-5-20251001",
        "anthropic:claude-opus-4-7",
    ],
    "quality": [
        "anthropic:claude-opus-4-7",
        "anthropic:claude-sonnet-4-6",
        "anthropic:claude-haiku-4-5-20251001",
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


def tier_menu(preset: str) -> list[str]:
    """Return the ordered list of ModelEntry ids for a preset's menu.

    dev/frugal resolve to "openrouter:"-prefixed allowlist ids;
    the other three return their static list. Unknown preset -> [].
    """
    if preset == "dev":
        return [f"openrouter:{s}" for s in DEV_TIER_SLUGS]
    if preset == "frugal":
        return [f"openrouter:{s}" for s in FRUGAL_TIER_SLUGS]
    return list(STATIC_TIER_MENUS.get(preset, []))
