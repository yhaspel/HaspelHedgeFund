"""Seed canonical ModelEntry rows on post_migrate."""
from __future__ import annotations

from decimal import Decimal

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
         price_in_per_mtok=Decimal("0.40"), price_out_per_mtok=Decimal("0.40")),
    dict(id="openrouter:qwen/qwen3.6-27b", provider="openrouter",
         display_name="Qwen3 27B (OpenRouter)", tier="hosted_open",
         context_window=131_072, supports_long_context=True,
         price_in_per_mtok=Decimal("0.15"), price_out_per_mtok=Decimal("0.30")),
    dict(id="openrouter:deepseek/deepseek-r1", provider="openrouter",
         display_name="DeepSeek R1 (OpenRouter)", tier="hosted_open",
         context_window=131_072, supports_long_context=True,
         price_in_per_mtok=Decimal("0.55"), price_out_per_mtok=Decimal("2.19")),
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
         context_window=131_072, supports_long_context=True,
         price_in_per_mtok=Decimal("0"), price_out_per_mtok=Decimal("0")),
    dict(id="openrouter:deepseek/deepseek-v4-flash:free", provider="openrouter",
         display_name="DeepSeek V4 Flash (OpenRouter Free)", tier="hosted_open",
         context_window=1_048_576, supports_long_context=True,
         price_in_per_mtok=Decimal("0"), price_out_per_mtok=Decimal("0")),
    dict(id="openrouter:qwen/qwen3-coder:free", provider="openrouter",
         display_name="Qwen3 Coder (OpenRouter Free)", tier="hosted_open",
         context_window=1_048_576, supports_long_context=True,
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
        ModelEntry.objects.update_or_create(id=spec["id"], defaults=spec)
