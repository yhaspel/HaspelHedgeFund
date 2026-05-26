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

# Hybrid fallback when no local model has been discovered. Picked so the
# existing hybrid-without-Ollama behavior is byte-identical to before the
# P3-C re-wire (this is the slug hybrid previously hardcoded).
HYBRID_LOCAL_FALLBACK = "openrouter:meta-llama/llama-3.3-70b-instruct"

PRESETS: dict[str, dict[str, str]] = {
    # Dev preset: every agent on an OpenRouter :free slug. Each persona is
    # mapped explicitly to a distinct free model so the council debates
    # with model diversity and a single provider 429 takes down only one
    # persona's call. Orchestration stays on the strongest free pick.
    "dev": {
        # Persona spread (8 personas, 8 distinct free slugs from DEV_TIER_SLUGS):
        "buffett": "openrouter:openai/gpt-oss-120b:free",
        "munger": "openrouter:nvidia/nemotron-3-super-120b-a12b:free",
        "graham": "openrouter:deepseek/deepseek-v4-flash:free",
        "wood": "openrouter:google/gemma-4-31b-it:free",
        "druckenmiller": "openrouter:z-ai/glm-4.5-air:free",
        "burry": "openrouter:minimax/minimax-m2.5:free",
        "damodaran": "openrouter:arcee-ai/trinity-large-thinking:free",
        "lynch": "openrouter:qwen/qwen3-coder:free",
        # Fallback for any future ninth persona — keep the wildcard.
        "*persona*": "openrouter:openai/gpt-oss-120b:free",
        "*analytical*": "openrouter:google/gemma-4-31b-it:free",
        "macro": "openrouter:deepseek/deepseek-v4-flash:free",
        "news_digest": "openrouter:nvidia/nemotron-3-super-120b-a12b:free",
        "risk_manager": "openrouter:nvidia/nemotron-3-super-120b-a12b:free",
        "portfolio_manager": "openrouter:openai/gpt-oss-120b:free",
        "cio": "openrouter:nvidia/nemotron-3-super-120b-a12b:free",
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
        "*persona*": "anthropic:claude-opus-4-7",
        "portfolio_manager": "anthropic:claude-opus-4-7",
        "risk_manager": "anthropic:claude-opus-4-7",
        "cio": "anthropic:claude-opus-4-7",
        "*analytical*": "anthropic:claude-sonnet-4-6",
        "macro": "anthropic:claude-sonnet-4-6",
        "news_digest": "anthropic:claude-sonnet-4-6",
    },
    # Frugal: each persona on a distinct cheap slug (FRUGAL_TIER_SLUGS),
    # orchestration on the strongest pick.
    "frugal": {
        "buffett": "openrouter:meta-llama/llama-3.3-70b-instruct",
        "munger": "openrouter:qwen/qwen3-235b-a22b-2507",
        "graham": "openrouter:openai/gpt-oss-120b",
        "wood": "openrouter:google/gemma-3-27b-it",
        "druckenmiller": "openrouter:nvidia/nemotron-3-nano-30b-a3b",
        "burry": "openrouter:mistralai/mistral-small-3.2-24b-instruct",
        "damodaran": "openrouter:z-ai/glm-4-32b",
        "lynch": "openrouter:qwen/qwen3.6-27b",
        "*persona*": "openrouter:qwen/qwen3.6-27b",
        "*": "openrouter:qwen/qwen3.6-27b",
        "portfolio_manager": "openrouter:qwen/qwen3-235b-a22b-2507",
        "cio": "openrouter:qwen/qwen3-235b-a22b-2507",
        "risk_manager": "openrouter:meta-llama/llama-3.3-70b-instruct",
    },
    "hybrid": {
        "*analytical*": "<local-tier-a>",
        "macro": "<local-tier-a>",
        "news_digest": "<local-tier-a>",
        "*persona*": "anthropic:claude-haiku-4-5-20251001",
        "risk_manager": "anthropic:claude-sonnet-4-6",
        "portfolio_manager": "anthropic:claude-sonnet-4-6",
        "cio": "anthropic:claude-sonnet-4-6",
    },
}


def expand_preset(preset: str, local_tier_a: str | None = None) -> dict[str, str]:
    """Return {agent_name: model_id} with wildcards expanded.

    The `<local-tier-a>` token (used in 'hybrid') resolves to `local_tier_a`
    when a local model was discovered, else to `HYBRID_LOCAL_FALLBACK`. It
    is *always* resolved — the literal token must never leak out as a model
    id.
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
    placeholder_target = local_tier_a or HYBRID_LOCAL_FALLBACK
    out = {k: (placeholder_target if v == "<local-tier-a>" else v) for k, v in out.items()}
    return out
