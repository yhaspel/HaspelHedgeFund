from .base import *  # noqa: F401,F403

DEBUG = True

# P2n: dev keeps platform-key fallback enabled so local workflows that rely on
# env vars (FMP_API_KEY etc.) continue to work without per-user setup.
ALLOW_PLATFORM_DATA_KEYS = True

# Local development never uses frontier models. Route every agent to the cheap
# `dev` preset (Qwen via OpenRouter) so strategies that never deliberately
# picked a preset can't leak Anthropic frontier spend during development.
# A strategy that explicitly selects another preset (frugal/research/quality)
# is still honored — see apps/portfolios/tasks._resolve_model_overrides.
LLM_DEFAULT_PRESET = "dev"

# Hard belt: even if a strategy explicitly picks the "frugal" / "research" /
# "quality" preset, or a user's per-agent overrides accidentally omit a few
# agents, the dev environment must never call the Anthropic API. Pairs with
# the DEFAULT_MODELS swap in hedgefund_agents/registry.py so fall-through goes
# to OpenRouter, not Haiku.
BLOCK_ANTHROPIC = True

# Restrict every model selector in the UI to free OpenRouter slugs in dev.
# Paired with the dev preset (apps/models_catalog/presets.py "dev") so the
# entire local environment is zero-spend by default.
LLM_FREE_ONLY = True
