"""Per-model pricing ($ per 1M tokens). Update as providers change prices.

This table is intentionally small in P1 — only the models we exercise.
P2d (Model Selector UI) will load this from a database table editable by
operators.
"""
from __future__ import annotations

from dataclasses import dataclass


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
    "qwen/qwen-3-32b": ModelPrice(0.15, 0.30),
    "qwen/qwen3.6-27b": ModelPrice(0.32, 3.20),
    "meta-llama/llama-3.3-70b-instruct": ModelPrice(0.40, 0.40),
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    price = PRICING.get(model)
    if price is None:
        return 0.0
    return (
        prompt_tokens / 1_000_000 * price.input_per_mtok
        + completion_tokens / 1_000_000 * price.output_per_mtok
    )
