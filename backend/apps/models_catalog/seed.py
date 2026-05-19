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
]


def seed_models(sender, **kwargs):
    from .models import ModelEntry
    for spec in CANONICAL_MODELS:
        ModelEntry.objects.update_or_create(id=spec["id"], defaults=spec)
