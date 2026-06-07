"""Factories. The graph nodes ask for clients/providers by name; this
module decides which class to instantiate. Centralising the wiring here
makes it easy to swap (e.g., Ollama in P2+) without touching agents.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from django.conf import settings

from .llm.adapters import AnthropicClient, OllamaClient, OpenRouterClient
from .llm.client import LLMClient

log = logging.getLogger(__name__)


@lru_cache(maxsize=64)
def _make_client(provider: str, user_id: int | None, api_key: str, host: str) -> LLMClient:
    """Internal cache key is the (provider, user_id, key, host) tuple so
    we don't reuse a platform-key client where a user just saved a BYO key."""
    if provider == "anthropic":
        return AnthropicClient(api_key=api_key or None)
    if provider == "openrouter":
        return OpenRouterClient(api_key=api_key or None)
    if provider == "ollama":
        return OllamaClient(host=host or None)
    raise ValueError(f"Unknown LLM provider: {provider}")


def get_llm(provider: str, *, user_id: int | None = None, state: dict | None = None) -> LLMClient:
    """Return an LLM client honoring the user's BYO key when available.

    Lane-B E2E: when ``settings.E2E_STUB_LLM`` is set (dev/test only, via
    ``seed_e2e`` / the nightly job), every provider resolves to a deterministic
    stub so a triggered run/cycle completes instantly with zero token spend.

    Resolution order:
      1. If `user_id` (or `state["user_id"]`) is given and the user has a
         `ProviderKey` row with a stored key for `provider`, use it.
      2. Otherwise fall back to the platform env key (`settings.*_API_KEY`).
      3. Ollama: per-user host overrides the env `OLLAMA_HOST`.

    We log only provider + key-source — never plaintext.
    """
    if getattr(settings, "E2E_STUB_LLM", False):
        from .llm.adapters.stub import StubLLMClient
        return StubLLMClient()
    if user_id is None and state is not None:
        user_id = state.get("user_id")
    api_key = ""
    host = ""
    source = "platform"
    if user_id is not None:
        try:
            from apps.models_catalog.models import ProviderKey
            pk = ProviderKey.objects.filter(user_id=user_id).first()
        except Exception:
            pk = None
        if pk is not None:
            if provider in ("anthropic", "openrouter", "openai") and pk.has_key(provider):
                api_key = pk.get_key(provider)
                source = "user"
            if provider == "ollama" and pk.ollama_host:
                host = pk.ollama_host
                source = "user"
    log.info("llm_client provider=%s user_id=%s key_source=%s", provider, user_id, source)
    return _make_client(provider, user_id if source == "user" else None, api_key, host)


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
_PROD_PERSONA = ("anthropic", "claude-haiku-4-5-20251001")
_PROD_ANALYTICAL = ("openrouter", "meta-llama/llama-3.3-70b-instruct")
# Dev/blocked fallback: every agent on a free OpenRouter slug. Used when an
# environment sets BLOCK_ANTHROPIC=True so a missing per-agent override can
# never resolve to Haiku and silently spend credits. Nemotron-3-Super-120B
# chosen as the universal fallback — flagship-grade, $0/Mtok, and crucially
# NON-reasoning: the previous pick (GPT-OSS 120B) burns its budget on hidden
# thinking and emits empty content (the reasoning-exhaustion class behind the
# qwen3.6-27b hang). Same non-reasoning rule the dev/frugal presets follow;
# guarded by test_preset_invariants.
_BLOCKED_FALLBACK = ("openrouter", "nvidia/nemotron-3-super-120b-a12b:free")

_ALL_AGENT_NAMES = (
    "fundamentals", "technicals", "valuation", "sentiment",
    "macro", "news_digest",
    "buffett", "munger", "graham", "wood",
    "druckenmiller", "burry", "damodaran", "lynch",
    "risk_manager", "cio",
)


def _compute_default_models() -> dict[str, tuple[str, str]]:
    """Build the agent → (provider, model) map.

    When settings.BLOCK_ANTHROPIC is True, every entry is OpenRouter — even the
    persona slots that prod sends to Haiku. This is the second leg of the
    "no Anthropic in dev" guarantee: the adapter raise (in
    anthropic.py:__init__) is the safety net; this prevents the safety net
    from ever firing on a happy-path run.
    """
    if getattr(settings, "BLOCK_ANTHROPIC", False):
        return {name: _BLOCKED_FALLBACK for name in _ALL_AGENT_NAMES}
    persona = _PROD_PERSONA
    analytical = _PROD_ANALYTICAL
    return {
        "fundamentals": analytical, "technicals": analytical,
        "valuation": analytical, "sentiment": analytical,
        "macro": analytical, "news_digest": analytical,
        "buffett": persona, "munger": persona, "graham": persona,
        "wood": persona, "druckenmiller": persona, "burry": persona,
        "damodaran": persona, "lynch": persona,
        "risk_manager": persona, "cio": persona,
    }


DEFAULT_MODELS: dict[str, tuple[str, str]] = _compute_default_models()


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
