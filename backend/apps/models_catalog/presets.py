"""Named presets. Wildcards (`*persona*`, `*analytical*`, `*`) are expanded
against the registered agent list when applied to a run.
"""
from __future__ import annotations

PERSONA_AGENTS = {
    "buffett", "munger", "graham", "wood",
    "druckenmiller", "burry", "damodaran", "lynch",
}
ANALYTICAL_AGENTS = {"fundamentals", "technicals", "valuation", "sentiment"}
ALL_AGENTS = PERSONA_AGENTS | ANALYTICAL_AGENTS | {
    "macro", "news_digest", "risk_manager", "portfolio_manager", "cio",
}

PRESETS: dict[str, dict[str, str]] = {
    "dev": {
        "*": "openrouter:qwen/qwen3.6-27b",
    },
    "research": {
        "*persona*": "anthropic:claude-sonnet-4-6",
        "risk_manager": "anthropic:claude-sonnet-4-6",
        "portfolio_manager": "anthropic:claude-sonnet-4-6",
        "cio": "anthropic:claude-sonnet-4-6",
        "*analytical*": "anthropic:claude-haiku-4-5-20251001",
        "macro": "anthropic:claude-haiku-4-5-20251001",
        "news_digest": "anthropic:claude-sonnet-4-6",
    },
    # "quality" intentionally pays for the premium model on the decision
    # path (personas + PM + RM + CIO) per master plan §7.5. Analytical /
    # macro / news stay on Sonnet — Opus is overkill for structured-extraction
    # workloads and would burn budget for negligible quality gain.
    "quality": {
        "*persona*": "anthropic:claude-opus-4-6",
        "portfolio_manager": "anthropic:claude-opus-4-6",
        "risk_manager": "anthropic:claude-opus-4-6",
        "cio": "anthropic:claude-opus-4-6",
        "*analytical*": "anthropic:claude-sonnet-4-6",
        "macro": "anthropic:claude-sonnet-4-6",
        "news_digest": "anthropic:claude-sonnet-4-6",
    },
    "frugal": {
        "*": "openrouter:qwen/qwen3.6-27b",
        "portfolio_manager": "openrouter:meta-llama/llama-3.3-70b-instruct",
        "cio": "openrouter:meta-llama/llama-3.3-70b-instruct",
    },
    "hybrid": {
        "*analytical*": "openrouter:meta-llama/llama-3.3-70b-instruct",
        "macro": "openrouter:meta-llama/llama-3.3-70b-instruct",
        "news_digest": "openrouter:meta-llama/llama-3.3-70b-instruct",
        "*persona*": "anthropic:claude-haiku-4-5-20251001",
        "risk_manager": "anthropic:claude-sonnet-4-6",
        "portfolio_manager": "anthropic:claude-sonnet-4-6",
        "cio": "anthropic:claude-sonnet-4-6",
    },
}


def expand_preset(preset: str, local_tier_a: str | None = None) -> dict[str, str]:
    """Return {agent_name: model_id} with wildcards expanded.

    `<local-tier-a>` token (used in 'hybrid' if no local model is discovered)
    falls back to the cheapest hosted-open model.
    """
    if preset not in PRESETS:
        return {}
    rules = PRESETS[preset]
    out: dict[str, str] = {}
    for agent in ALL_AGENTS:
        if agent in rules:
            out[agent] = rules[agent]
            continue
        if agent in PERSONA_AGENTS and "*persona*" in rules:
            out[agent] = rules["*persona*"]
            continue
        if agent in ANALYTICAL_AGENTS and "*analytical*" in rules:
            out[agent] = rules["*analytical*"]
            continue
        if "*" in rules:
            out[agent] = rules["*"]
    if local_tier_a:
        out = {k: (local_tier_a if v == "<local-tier-a>" else v) for k, v in out.items()}
    return out
