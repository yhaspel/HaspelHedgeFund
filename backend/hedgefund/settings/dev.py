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
