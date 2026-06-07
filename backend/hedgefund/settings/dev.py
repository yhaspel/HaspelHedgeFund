from .base import *  # noqa: F401,F403

DEBUG = True

# Phase 8 — `manage.py seed_e2e` is permitted under dev settings only.
E2E_SEED_ALLOWED = True

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

# Paid OpenRouter routes (Qwen3.6 27B, Llama 3.3 70B, etc.) stay selectable in
# dev so the frugal / research / quality presets can be exercised locally —
# BLOCK_ANTHROPIC above is the actual cost-safety guard (it blocks the much
# pricier Anthropic direct API). Flip this back to True if you want a truly
# zero-spend local environment.
LLM_FREE_ONLY = False
