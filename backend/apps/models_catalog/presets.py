"""Named presets. Wildcards (`*persona*`, `*analytical*`, `*`) are expanded
against the registered agent list when applied to a run.
"""

from __future__ import annotations

PERSONA_AGENTS = {
    "buffett",
    "munger",
    "graham",
    "wood",
    "druckenmiller",
    "burry",
    "damodaran",
    "lynch",
}
ANALYTICAL_AGENTS = {"fundamentals", "technicals", "valuation", "sentiment"}
ALL_AGENTS = (
    PERSONA_AGENTS
    | ANALYTICAL_AGENTS
    | {
        "macro",
        "news_digest",
        "risk_manager",
        "portfolio_manager",
        "cio",
    }
)

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
        # Persona spread across DEV_TIER_SLUGS — LIVE, NON-REASONING :free
        # routes only (the old gpt-oss/qwen3-coder reasoning slugs and the
        # dead arcee/minimax routes are gone). The free non-reasoning pool is
        # smaller than 8 personas, so the personas SPAN the menu (some reused)
        # rather than 1-per-persona; the self-heal layer covers any that empty
        # out, and a provider 429 still only takes down its slug's share.
        # 2026-10-20: nemotron-3-super:free died upstream (404 while still
        # listed — run 1178), deepseek-v4-flash:free + glm-4.5-air:free were
        # delisted; re-spread over the re-curated DEV_TIER_SLUGS. Decision
        # roles (RM/PM/CIO) + news on the two strongest routes, on DIFFERENT
        # providers (Nvidia / Minimax) so one upstream outage can't blind all
        # three; gemma-4 (upstream-429'd all day on 2026-10-20) is kept off EVERY
        # role — it is a fallback-only ladder rung until it stabilises.
        "buffett": "openrouter:nvidia/nemotron-3.5-super-vl:free",
        "munger": "openrouter:minimax/minimax-m3.1-flash:free",
        "graham": "openrouter:nvidia/nemotron-3.5-lightning:free",
        "wood": "openrouter:dots-studio/dots-3-note-preview:free",
        "druckenmiller": "openrouter:xingchen-agi/xing4.0-29b-a4b:free",
        "burry": "openrouter:minimax/minimax-m3.1-flash:free",
        "damodaran": "openrouter:nvidia/nemotron-3.5-super-vl:free",
        "lynch": "openrouter:dots-studio/dots-3-note-preview:free",
        # Fallback for any future ninth persona — keep the wildcard.
        "*persona*": "openrouter:nvidia/nemotron-3.5-super-vl:free",
        "*analytical*": "openrouter:nvidia/nemotron-3.5-lightning:free",
        "macro": "openrouter:dots-studio/dots-3-note-preview:free",
        "news_digest": "openrouter:minimax/minimax-m3.1-flash:free",
        "risk_manager": "openrouter:nvidia/nemotron-3.5-super-vl:free",
        "portfolio_manager": "openrouter:minimax/minimax-m3.1-flash:free",
        "cio": "openrouter:nvidia/nemotron-3.5-super-vl:free",
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
    # Frugal: each persona on a distinct cheap NON-REASONING slug
    # (FRUGAL_TIER_SLUGS, all paid/stable). No reasoning routes on ANY role —
    # they burn the budget on hidden thinking and emit empty content (the
    # qwen/qwen3.6-27b hang that stalled runs 236/237). Orchestration + the
    # analytical catch-all on Llama 3.3 70B, the proven non-reasoning default.
    "frugal": {
        "buffett": "openrouter:meta-llama/llama-3.3-70b-instruct",
        "munger": "openrouter:nvidia/nemotron-3-nano-30b-a3b",
        "graham": "openrouter:mistralai/mistral-small-3.2-24b-instruct",
        "wood": "openrouter:google/gemma-3-27b-it",
        # druckenmiller was z-ai/glm-4-32b until OpenRouter delisted it
        # (2026-07 reconcile; the dead prefill 400'd every untouched frugal
        # submission until P13 healing). The slug stays in FRUGAL_TIER_SLUGS
        # (curation intent — the live sync governs its activity if it
        # returns); the preset pick moves to a live non-reasoning member.
        "druckenmiller": "openrouter:nvidia/nemotron-3-nano-30b-a3b",
        "burry": "openrouter:deepseek/deepseek-v4-flash",
        # damodaran reuses deepseek-v4-flash (validated clean); amazon/nova-lite-v1
        # was dropped after it degraded 4 council agents. 6 proven non-reasoning
        # models cover the 8 personas.
        "damodaran": "openrouter:deepseek/deepseek-v4-flash",
        "lynch": "openrouter:meta-llama/llama-3.3-70b-instruct",
        "*persona*": "openrouter:meta-llama/llama-3.3-70b-instruct",
        "*": "openrouter:meta-llama/llama-3.3-70b-instruct",
        "portfolio_manager": "openrouter:meta-llama/llama-3.3-70b-instruct",
        "cio": "openrouter:meta-llama/llama-3.3-70b-instruct",
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
    # P4-OFF: the all-local preset for offline mode. Every agent resolves to the
    # discovered Ollama model via the `<local>` token. Unlike hybrid's
    # `<local-tier-a>` (which falls back to a cloud slug when no local model is
    # found), `<local>` HARD-ERRORS with no local model — a cloud fallback would
    # defeat the offline guarantee (R3).
    "local": {"*": "<local>"},
}

# Presets a user may pick in the UI / online preset API. "local" is excluded: it
# is offline-only, forced by OFFLINE_MODE, and its `<local>` token needs a live
# Ollama probe that would 500 the online preset endpoints on a machine with no
# daemon. It is never user-selectable — the execution seams apply it directly.
SELECTABLE_PRESETS = [p for p in PRESETS if p != "local"]


def expand_preset(
    preset: str, local_tier_a: str | None = None, local_model: str | None = None
) -> dict[str, str]:
    """Return {agent_name: model_id} with wildcards expanded.

    The `<local-tier-a>` token (used in 'hybrid') resolves to `local_tier_a`
    when a local model was discovered, else to `HYBRID_LOCAL_FALLBACK`. The
    `<local>` token (used in 'local', the offline preset) resolves to
    `local_model` when given, else via live Ollama discovery — and HARD-ERRORS
    (never a cloud fallback) when no local model exists. Both tokens are
    *always* resolved — the literal token must never leak out as a model id.
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
    if any(v == "<local>" for v in out.values()):
        if local_model is None:
            from .offline import resolve_local_model  # lazy: avoid import cycle

            local_model = resolve_local_model()
        out = {k: (local_model if v == "<local>" else v) for k, v in out.items()}
    return out
