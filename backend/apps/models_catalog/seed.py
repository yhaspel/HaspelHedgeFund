"""Seed canonical ModelEntry rows on post_migrate.

Allowlisted (dev/frugal) rows are inserted with `get_or_create` so a
later live `sync_tier_models()` fetch is never reverted by a subsequent
`post_migrate` run (P3-C §6.5). Non-allowlisted rows keep
`update_or_create` so a seed edit still propagates.
"""
from __future__ import annotations

from decimal import Decimal

from .tier_menus import (
    DEV_TIER_SLUGS,
    FRUGAL_PRICE_CEILING_IN,
    FRUGAL_PRICE_CEILING_OUT,
    FRUGAL_TIER_SLUGS,
    STATIC_TIER_MENUS,
)

_FETCHED_IDS = {f"openrouter:{s}" for s in (*DEV_TIER_SLUGS, *FRUGAL_TIER_SLUGS)}

CANONICAL_MODELS = [
    # Anthropic frontier
    dict(id="anthropic:claude-sonnet-4-6", provider="anthropic",
         display_name="Claude Sonnet 4.6", tier="frontier",
         context_window=200_000, supports_caching=True,
         supports_long_context=True,
         price_in_per_mtok=Decimal("3.00"), price_out_per_mtok=Decimal("15.00")),
    dict(id="anthropic:claude-opus-4-7", provider="anthropic",
         display_name="Claude Opus 4.7", tier="frontier",
         context_window=200_000, supports_caching=True,
         supports_long_context=True,
         price_in_per_mtok=Decimal("15.00"), price_out_per_mtok=Decimal("75.00")),
    dict(id="anthropic:claude-haiku-4-5-20251001", provider="anthropic",
         display_name="Claude Haiku 4.5", tier="fast_cheap",
         context_window=200_000, supports_caching=True,
         supports_long_context=True,
         price_in_per_mtok=Decimal("1.00"), price_out_per_mtok=Decimal("5.00")),
    # OpenRouter hosted-open
    dict(id="openrouter:meta-llama/llama-3.3-70b-instruct", provider="openrouter",
         display_name="Llama 3.3 70B (OpenRouter)", tier="hosted_open",
         context_window=131_072, supports_long_context=True,
         price_in_per_mtok=Decimal("0.10"), price_out_per_mtok=Decimal("0.32")),
    dict(id="openrouter:qwen/qwen3.6-27b", provider="openrouter",
         display_name="Qwen3 27B (OpenRouter)", tier="hosted_open",
         context_window=131_072, supports_long_context=True, supports_reasoning=True,
         price_in_per_mtok=Decimal("0.30"), price_out_per_mtok=Decimal("3.20")),
    # NB: deepseek/deepseek-r1 was removed (delisted upstream; superseded by the
    # reasoning allowlist's deepseek-v4-pro). Migration 0006 hard-deletes the row.
    # "deepseek-r1" stays in _REASONING_SLUGS — still a valid reasoning-family
    # marker (adapter effort cap + sentiment-model exclusion in test_market_news).
    # Cost-effective OpenRouter paid models picked for load + reasoning quality.
    # Pricing verified from openrouter.ai/api/v1/models; verify_openrouter_pricing
    # keeps these in sync.
    dict(id="openrouter:qwen/qwen3-235b-a22b-2507", provider="openrouter",
         display_name="Qwen3 235B A22B Instruct (OpenRouter)", tier="hosted_open",
         context_window=262_144, supports_long_context=True, supports_reasoning=True,
         price_in_per_mtok=Decimal("0.071"), price_out_per_mtok=Decimal("0.100")),
    dict(id="openrouter:openai/gpt-oss-120b", provider="openrouter",
         display_name="GPT-OSS 120B (OpenRouter)", tier="hosted_open",
         context_window=131_072, supports_long_context=True, supports_reasoning=True,
         price_in_per_mtok=Decimal("0.039"), price_out_per_mtok=Decimal("0.180")),
    dict(id="openrouter:nvidia/nemotron-3-nano-30b-a3b", provider="openrouter",
         display_name="NVIDIA Nemotron 3 Nano 30B A3B (OpenRouter)", tier="hosted_open",
         context_window=262_144, supports_long_context=True,
         price_in_per_mtok=Decimal("0.050"), price_out_per_mtok=Decimal("0.200")),
    dict(id="openrouter:mistralai/mistral-small-3.2-24b-instruct", provider="openrouter",
         display_name="Mistral Small 3.2 24B (OpenRouter)", tier="hosted_open",
         context_window=128_000, supports_long_context=True,
         price_in_per_mtok=Decimal("0.075"), price_out_per_mtok=Decimal("0.200")),
    dict(id="openrouter:google/gemma-3-27b-it", provider="openrouter",
         display_name="Gemma 3 27B (OpenRouter)", tier="hosted_open",
         context_window=131_072, supports_long_context=True,
         price_in_per_mtok=Decimal("0.080"), price_out_per_mtok=Decimal("0.160")),
    dict(id="openrouter:amazon/nova-lite-v1", provider="openrouter",
         display_name="Amazon Nova Lite 1.0 (OpenRouter)", tier="hosted_open",
         context_window=300_000, supports_long_context=True,
         price_in_per_mtok=Decimal("0.060"), price_out_per_mtok=Decimal("0.240")),
    dict(id="openrouter:deepseek/deepseek-v4-flash", provider="openrouter",
         display_name="DeepSeek V4 Flash (OpenRouter)", tier="hosted_open",
         context_window=1_048_576, supports_long_context=True,
         price_in_per_mtok=Decimal("0.100"), price_out_per_mtok=Decimal("0.200")),
    dict(id="openrouter:qwen/qwen3.5-flash-02-23", provider="openrouter",
         display_name="Qwen3.5 Flash (OpenRouter)", tier="hosted_open",
         context_window=1_000_000, supports_long_context=True, supports_reasoning=True,
         price_in_per_mtok=Decimal("0.065"), price_out_per_mtok=Decimal("0.260")),
    dict(id="openrouter:qwen/qwen3-coder-30b-a3b-instruct", provider="openrouter",
         display_name="Qwen3 Coder 30B A3B (OpenRouter)", tier="hosted_open",
         context_window=160_000, supports_long_context=True, supports_reasoning=True,
         price_in_per_mtok=Decimal("0.070"), price_out_per_mtok=Decimal("0.270")),
    dict(id="openrouter:z-ai/glm-4-32b", provider="openrouter",
         display_name="GLM 4 32B (OpenRouter)", tier="hosted_open",
         context_window=128_000, supports_long_context=True,
         price_in_per_mtok=Decimal("0.100"), price_out_per_mtok=Decimal("0.100")),
    # Reasoning allowlist (REASONING_TIER_SLUGS) — offered in the research/quality
    # menus only; never a decision-role default. Statically priced; kept fresh by
    # `verify_openrouter_pricing`. Pricing per openrouter.ai/api/v1/models. Context
    # windows are best-effort (these aren't live-synced; verify only audits price).
    dict(id="openrouter:deepseek/deepseek-v4-pro", provider="openrouter",
         display_name="DeepSeek V4 Pro (OpenRouter)", tier="hosted_open",
         context_window=1_048_576, supports_long_context=True, supports_reasoning=True,
         price_in_per_mtok=Decimal("0.4350"), price_out_per_mtok=Decimal("0.8700")),
    dict(id="openrouter:z-ai/glm-5.1", provider="openrouter",
         display_name="GLM 5.1 (OpenRouter)", tier="hosted_open",
         context_window=200_000, supports_long_context=True, supports_reasoning=True,
         price_in_per_mtok=Decimal("0.9800"), price_out_per_mtok=Decimal("3.0800")),
    dict(id="openrouter:nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
         provider="openrouter",
         display_name="NVIDIA Nemotron 3 Nano Omni 30B (Reasoning, OpenRouter Free)",
         tier="hosted_open",
         context_window=262_144, supports_long_context=True, supports_reasoning=True,
         price_in_per_mtok=Decimal("0"), price_out_per_mtok=Decimal("0")),
    # OpenRouter free-tier. price=0 is the "free" marker; the
    # verify_openrouter_pricing command keeps these in sync with OpenRouter
    # and stamps last_verified_at on every row.
    dict(id="openrouter:arcee-ai/trinity-large-thinking:free", provider="openrouter",
         display_name="Arcee Trinity Large (Thinking, OpenRouter Free)", tier="hosted_open",
         context_window=262_144, supports_long_context=True,
         price_in_per_mtok=Decimal("0"), price_out_per_mtok=Decimal("0")),
    dict(id="openrouter:nvidia/nemotron-3-super-120b-a12b:free", provider="openrouter",
         display_name="NVIDIA Nemotron 3 Super 120B (OpenRouter Free)", tier="hosted_open",
         context_window=1_000_000, supports_long_context=True,
         price_in_per_mtok=Decimal("0"), price_out_per_mtok=Decimal("0")),
    dict(id="openrouter:openai/gpt-oss-120b:free", provider="openrouter",
         display_name="GPT-OSS 120B (OpenRouter Free)", tier="hosted_open",
         context_window=131_072, supports_long_context=True, supports_reasoning=True,
         price_in_per_mtok=Decimal("0"), price_out_per_mtok=Decimal("0")),
    dict(id="openrouter:deepseek/deepseek-v4-flash:free", provider="openrouter",
         display_name="DeepSeek V4 Flash (OpenRouter Free)", tier="hosted_open",
         context_window=1_048_576, supports_long_context=True,
         price_in_per_mtok=Decimal("0"), price_out_per_mtok=Decimal("0")),
    dict(id="openrouter:qwen/qwen3-coder:free", provider="openrouter",
         display_name="Qwen3 Coder (OpenRouter Free)", tier="hosted_open",
         context_window=1_048_576, supports_long_context=True, supports_reasoning=True,
         price_in_per_mtok=Decimal("0"), price_out_per_mtok=Decimal("0")),
    dict(id="openrouter:minimax/minimax-m2.5:free", provider="openrouter",
         display_name="MiniMax M2.5 (OpenRouter Free)", tier="hosted_open",
         context_window=204_800, supports_long_context=True,
         price_in_per_mtok=Decimal("0"), price_out_per_mtok=Decimal("0")),
    dict(id="openrouter:google/gemma-4-31b-it:free", provider="openrouter",
         display_name="Gemma 4 31B (OpenRouter Free)", tier="hosted_open",
         context_window=262_144, supports_long_context=True,
         price_in_per_mtok=Decimal("0"), price_out_per_mtok=Decimal("0")),
    dict(id="openrouter:z-ai/glm-4.5-air:free", provider="openrouter",
         display_name="GLM 4.5 Air (OpenRouter Free)", tier="hosted_open",
         context_window=131_072, supports_long_context=True,
         price_in_per_mtok=Decimal("0"), price_out_per_mtok=Decimal("0")),
    dict(id="openrouter:poolside/laguna-m.1:free", provider="openrouter",
         display_name="Poolside Laguna M.1 (OpenRouter Free)", tier="hosted_open",
         context_window=131_072, supports_long_context=True,
         price_in_per_mtok=Decimal("0"), price_out_per_mtok=Decimal("0")),
]


def seed_models(sender, **kwargs):
    from .models import ModelEntry
    for spec in CANONICAL_MODELS:
        if spec["id"] in _FETCHED_IDS:
            # Fetched rows: cold-start baseline only — never overwrite live
            # metadata that sync_tier_models() may have written.
            ModelEntry.objects.get_or_create(id=spec["id"], defaults=spec)
        else:
            ModelEntry.objects.update_or_create(id=spec["id"], defaults=spec)
    # Tier config is seeded AFTER the catalog so its FKs/membership resolve. Runs
    # on every post_migrate; idempotent + cold-start-guarded (see seed_tiers).
    seed_tiers(sender, **kwargs)


# Per-tier policy + default for the DB-backed TierConfig/TierMembership, seeded
# byte-identically from the tier_menus DEFAULT_* constants + the PRESETS persona-
# wildcard defaults (the same values graphs.views._tier_default produced). This
# is the baseline; operators edit the DB rows afterward and the seed never
# reverts those edits (get_or_create + cold-start membership guard).
_TIER_SEED: dict[str, dict] = {
    "dev": dict(
        default_model="openrouter:nvidia/nemotron-3-super-120b-a12b:free",
        is_free_only=True, price_ceiling_in=None, price_ceiling_out=None,
        allow_reasoning=False, is_live_synced=True,
        members=[f"openrouter:{s}" for s in DEV_TIER_SLUGS],
    ),
    "frugal": dict(
        default_model="openrouter:meta-llama/llama-3.3-70b-instruct",
        is_free_only=False,
        price_ceiling_in=FRUGAL_PRICE_CEILING_IN,
        price_ceiling_out=FRUGAL_PRICE_CEILING_OUT,
        allow_reasoning=False, is_live_synced=True,
        members=[f"openrouter:{s}" for s in FRUGAL_TIER_SLUGS],
    ),
    "research": dict(
        default_model="anthropic:claude-sonnet-4-6",
        is_free_only=False, price_ceiling_in=None, price_ceiling_out=None,
        allow_reasoning=True, is_live_synced=False,
        members=list(STATIC_TIER_MENUS["research"]),
    ),
    "quality": dict(
        default_model="anthropic:claude-opus-4-7",
        is_free_only=False, price_ceiling_in=None, price_ceiling_out=None,
        allow_reasoning=True, is_live_synced=False,
        members=list(STATIC_TIER_MENUS["quality"]),
    ),
    "hybrid": dict(
        default_model="anthropic:claude-haiku-4-5-20251001",
        is_free_only=False, price_ceiling_in=None, price_ceiling_out=None,
        allow_reasoning=False, is_live_synced=False,
        members=list(STATIC_TIER_MENUS["hybrid"]),
    ),
}


def seed_tiers(sender=None, **kwargs):
    """Seed TierConfig + TierMembership from the baseline (idempotent).

    Membership is seeded ONLY when the TierConfig row is first created (true cold
    start), keyed off get_or_create's `created` flag — NOT off "has zero members"
    — so an operator who deliberately empties a tier (PUT members:[]) is not
    silently re-populated on the next post_migrate. Skips ids without a
    ModelEntry row defensively.
    """
    from .models import ModelEntry, TierConfig, TierMembership
    for tier_name, spec in _TIER_SEED.items():
        default_obj = ModelEntry.objects.filter(id=spec["default_model"]).first()
        tc, created = TierConfig.objects.get_or_create(
            tier_name=tier_name,
            defaults=dict(
                default_model=default_obj,
                is_free_only=spec["is_free_only"],
                price_ceiling_in=spec["price_ceiling_in"],
                price_ceiling_out=spec["price_ceiling_out"],
                allow_reasoning=spec["allow_reasoning"],
                is_live_synced=spec["is_live_synced"],
            ),
        )
        if created:
            for ordering, mid in enumerate(spec["members"]):
                model_obj = ModelEntry.objects.filter(id=mid).first()
                if model_obj is None:
                    continue
                TierMembership.objects.create(
                    tier=tc, model=model_obj, ordering=ordering
                )
