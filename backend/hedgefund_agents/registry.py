"""Factories. The graph nodes ask for clients/providers by name; this
module decides which class to instantiate. Centralising the wiring here
makes it easy to swap (e.g., Ollama in P2+) without touching agents.
"""
from __future__ import annotations

from functools import lru_cache

from apps.data.providers import EdgarProvider, FmpProvider
from apps.data.providers.fred import FredProvider
from apps.data.providers.news import NewsService

from .llm.adapters import AnthropicClient, OpenRouterClient
from .llm.client import LLMClient


@lru_cache(maxsize=4)
def get_llm(provider: str) -> LLMClient:
    if provider == "anthropic":
        return AnthropicClient()
    if provider == "openrouter":
        return OpenRouterClient()
    raise ValueError(f"Unknown LLM provider: {provider}")


def get_data_provider() -> FmpProvider:
    return FmpProvider()


def get_filings_provider() -> EdgarProvider:
    return EdgarProvider()


def get_macro_provider() -> FredProvider:
    return FredProvider()


def get_news_service() -> NewsService:
    return NewsService()


# Default (provider, model) per agent — used unless run.model_overrides says otherwise.
# Qwen3.6 27B (reasoning model) had brutal wall-time on persona calls (2-3 min
# each). Haiku 4.5 finishes each call in 2-8s with comparable structured-output
# quality at this scale; flip to it as the global default 2026-05-17.
# Tiered defaults after the 2026-05-18 benchmark on 3-ticker × 5-day slice
# (scripts/bench_models.py): Llama 3.3 70B via OpenRouter scored 100% action
# agreement vs Haiku 4.5 at 4.7× lower cost ($0.24 vs $1.14 over 15 council
# invocations). Used for ANALYTICAL + macro/news (structured extraction tasks
# that don't need frontier synthesis). Personas + risk/PM stay on Haiku since
# they synthesize multi-source context.
_PERSONA = ("anthropic", "claude-haiku-4-5-20251001")
_ANALYTICAL = ("openrouter", "meta-llama/llama-3.3-70b-instruct")
DEFAULT_MODELS: dict[str, tuple[str, str]] = {
    "fundamentals": _ANALYTICAL,
    "technicals": _ANALYTICAL,
    "valuation": _ANALYTICAL,
    "sentiment": _ANALYTICAL,
    "macro": _ANALYTICAL,
    "news_digest": _ANALYTICAL,
    "buffett": _PERSONA,
    "munger": _PERSONA,
    "graham": _PERSONA,
    "wood": _PERSONA,
    "druckenmiller": _PERSONA,
    "burry": _PERSONA,
    "damodaran": _PERSONA,
    "lynch": _PERSONA,
    "risk_manager": _PERSONA,
    "cio": _PERSONA,
}


# Catalog for GET /api/models/ (stub for P2d).
MODEL_CATALOG = [
    {"id": "anthropic:claude-sonnet-4-6", "name": "Claude Sonnet 4.6", "tier": "frontier",
     "input_per_mtok": 3.0, "output_per_mtok": 15.0},
    {"id": "anthropic:claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5", "tier": "fast",
     "input_per_mtok": 1.0, "output_per_mtok": 5.0},
    {"id": "openrouter:anthropic/claude-sonnet-4.6", "name": "Sonnet 4.6 (via OpenRouter)",
     "tier": "frontier", "input_per_mtok": 3.15, "output_per_mtok": 15.75},
    {"id": "openrouter:qwen/qwen3.6-27b", "name": "Qwen3 32B (OpenRouter)", "tier": "cheap",
     "input_per_mtok": 0.15, "output_per_mtok": 0.30},
    {"id": "openrouter:meta-llama/llama-3.3-70b-instruct", "name": "Llama 3.3 70B (OpenRouter)",
     "tier": "balanced", "input_per_mtok": 0.40, "output_per_mtok": 0.40},
]
