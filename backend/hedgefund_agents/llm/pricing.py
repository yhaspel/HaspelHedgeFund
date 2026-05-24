"""Per-model pricing ($ per 1M tokens). Update as providers change prices.

This table is intentionally small in P1 — only the models we exercise.
P2d (Model Selector UI) will load this from a database table editable by
operators.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings

log = logging.getLogger(__name__)

# Sentinel cost recorded when a model's price isn't in PRICING and strict
# mode is off. Negative so it can't masquerade as a $0.00 (free) call —
# downstream code (LLMCall serializer, totals) treats < 0 as "unknown".
UNKNOWN_COST_SENTINEL = -1.0

# Provider qualifiers used in ModelEntry.id ("anthropic:claude-...", "openrouter:openai/...").
# Adapters call estimate_cost with the BARE slug (no prefix), so the catalog lookup needs
# to try both shapes regardless of whether the bare slug itself happens to contain a ":"
# (free-tier OpenRouter routes always do, e.g. "openai/gpt-oss-120b:free").
_KNOWN_PROVIDER_PREFIXES = ("anthropic:", "openrouter:", "ollama:")


class UnknownModelPriceError(LookupError):
    """Raised when `estimate_cost` is asked for a model with no PRICING entry
    and `LLM_REQUIRE_KNOWN_PRICES` is True. Add the model to PRICING (or seed
    a `ModelEntry` row in `models_catalog`) before invoking it."""


@dataclass(frozen=True)
class ModelPrice:
    input_per_mtok: float
    output_per_mtok: float


PRICING: dict[str, ModelPrice] = {
    # Anthropic direct (USD per 1M tokens).
    "claude-sonnet-4-6": ModelPrice(3.0, 15.0),
    "claude-haiku-4-5-20251001": ModelPrice(1.0, 5.0),
    # OpenRouter routes — provider markup baked in (~5%).
    "anthropic/claude-sonnet-4.6": ModelPrice(3.15, 15.75),
    "anthropic/claude-haiku-4.5": ModelPrice(1.05, 5.25),
    # Note: "qwen/qwen-3-32b" was rejected by OpenRouter (invalid slug,
    # 2026-05-18 bench). Removed until we verify the live slug.
    "qwen/qwen3.6-27b": ModelPrice(0.32, 3.20),
    "meta-llama/llama-3.3-70b-instruct": ModelPrice(0.40, 0.40),
}


def lookup_catalog_price(model: str) -> ModelPrice | None:
    """Read pricing from `ModelEntry` (P2d catalog) if available.

    Catalog rows are the single source of truth for cost estimates and
    recorded `LLMCall.cost_usd`. The static `PRICING` table below remains
    only as a bootstrap/fallback for environments where migrations haven't
    populated the catalog yet (tests that don't load fixtures, dev runs
    against a fresh DB).
    """
    try:
        from apps.models_catalog.models import ModelEntry
    except Exception:
        return None
    # `model` may be a bare slug ("claude-sonnet-4-6", "openai/gpt-oss-120b:free")
    # or provider-qualified ("anthropic:claude-sonnet-4-6"). Build candidates that
    # cover both shapes — and don't rely on a naive `":" in model` check, because
    # free-tier OpenRouter slugs embed ":free" in the bare slug itself.
    candidates: set[str] = {model}
    matched_prefix = next(
        (p for p in _KNOWN_PROVIDER_PREFIXES if model.startswith(p)),
        None,
    )
    if matched_prefix is not None:
        candidates.add(model[len(matched_prefix):])
    else:
        candidates.update(f"{p}{model}" for p in _KNOWN_PROVIDER_PREFIXES)
    try:
        entry = ModelEntry.objects.filter(id__in=candidates, is_active=True).first()
    except Exception:
        return None
    if entry is None or entry.price_in_per_mtok is None or entry.price_out_per_mtok is None:
        return None
    return ModelPrice(float(entry.price_in_per_mtok), float(entry.price_out_per_mtok))


def estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    strict: bool | None = None,
) -> float:
    """Return $ cost for a call, or `UNKNOWN_COST_SENTINEL` (-1.0) when the
    model is not priced and strict mode is disabled.

    Strict mode (the default) raises `UnknownModelPriceError`. Disable it
    via `LLM_REQUIRE_KNOWN_PRICES=False` for dev exploration, but never silently
    record an unknown-priced call as $0.00 — that's the failure mode this
    function exists to prevent.
    """
    if strict is None:
        strict = bool(getattr(settings, "LLM_REQUIRE_KNOWN_PRICES", True))
    # Catalog (ModelEntry) wins; static PRICING is a bootstrap fallback.
    price = lookup_catalog_price(model)
    if price is None:
        price = PRICING.get(model)
        if price is not None:
            log.debug("pricing fallback (static table) for model=%r", model)
    if price is None:
        if strict:
            raise UnknownModelPriceError(
                f"No price entry for model {model!r}. Add it to "
                "`hedgefund_agents.llm.pricing.PRICING` or set "
                "LLM_REQUIRE_KNOWN_PRICES=False to record the call as unknown."
            )
        log.warning(
            "Unknown model price for %r — recording cost as 'unknown' (%s).",
            model,
            UNKNOWN_COST_SENTINEL,
        )
        return UNKNOWN_COST_SENTINEL
    return (
        prompt_tokens / 1_000_000 * price.input_per_mtok
        + completion_tokens / 1_000_000 * price.output_per_mtok
    )
