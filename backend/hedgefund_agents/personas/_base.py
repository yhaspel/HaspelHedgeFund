"""Shared persona scaffolding.

Each persona supplies a system prompt + an `AgentSpec`; this module
turns that into the (1) registered version (2) graph node callable that
reads fundamentals/technicals/valuation/sentiment + filings and writes a
`PersonaOutput` to `state[<name>]`.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from .._persist import record_llm_call
from ..base import AgentState, pick_model
from ..investor_profile_block import PERSONA_FRAMING, format_profile_block
from ..llm.client import Message
from ..llm.structured import call_structured
from ..outputs import PersonaOutput
from ..registry import DEFAULT_MODELS, get_llm
from ..versioning import AGENT_VERSIONS, AgentSpec, register

# Appended to every persona system prompt. The PersonaOutput schema now caps
# thesis at 600 chars and key_risks at 4 entries; without this hint reasoning
# models still produce 2-3 paragraph theses that get truncated by the schema
# validator (forcing a retry that burns the whole output budget). Asking
# explicitly cuts persona output ~5× and tracks with downstream cost.
TERSE_OUTPUT_INSTRUCTION = (
    "\n\nOUTPUT BUDGET: be terse. Thesis ≤100 words (≤600 chars), "
    "≤3 key risks. Personas downstream of you don't read the prose — they "
    "consume the structured signal + confidence. Save tokens for them."
)


@lru_cache(maxsize=1)
def _sector_context_prefix() -> str:
    p = Path(__file__).with_name("sector_context.md")
    return p.read_text(encoding="utf-8")


def make_persona_node(spec: AgentSpec) -> Callable[[AgentState], AgentState]:
    register(spec)
    name = spec.agent_name

    def node(state: AgentState) -> AgentState:
        ticker = state["ticker"]
        as_of = state["as_of_date"]
        is_sector = state.get("flavor") == "sector_rotation"

        if is_sector:
            # ETFs have no per-company filings; persona reasons over sector inputs.
            ctx = {
                "technicals": state.get("technicals", {}),
                "macro": state.get("macro", {}),
                "news_digest": state.get("news_digest", {}),
                "sector": state.get("sector", ""),
                "theme": state.get("theme", ""),
            }
            user = (
                f"Sector / theme ETF: {ticker}\nAs-of: {as_of.isoformat()}\n\n"
                f"SECTOR INPUTS:\n{json.dumps(ctx, indent=2, default=str)}\n\n"
                "Produce your PersonaOutput JSON now."
            )
            system_prompt = _sector_context_prefix() + "\n\n---\n\n" + spec.prompt + TERSE_OUTPUT_INSTRUCTION
        else:
            # Filings are best-effort. Some providers (EDGAR) raise LookupError
            # for tickers without a CIK (e.g. ETFs accidentally screened by an
            # equity strategy). Treat any failure as "no filings available" and
            # let the persona reason on the rest of the inputs.
            try:
                filings = state["filings_provider"].get_recent_filings(
                    ticker, as_of=as_of, form_types=["10-K", "10-Q"], limit=2
                )
            except Exception:
                filings = []
            filing_block = (
                "\n\n".join(
                    f"### {f.form_type} filed {f.filed_at.isoformat()} "
                    f"(period ending {f.period_end.isoformat()})\n{f.text_excerpt}"
                    for f in filings
                )
                or "(no recent filings available)"
            )
            ctx = {
                "fundamentals": state.get("fundamentals", {}),
                "technicals": state.get("technicals", {}),
                "valuation": state.get("valuation", {}),
                "sentiment": state.get("sentiment", {}),
                "macro": state.get("macro", {}),
                "news_digest": state.get("news_digest", {}),
            }
            user = (
                f"Ticker: {ticker}\nAs-of: {as_of.isoformat()}\n\n"
                f"ANALYTICAL INPUTS:\n{json.dumps(ctx, indent=2, default=str)}\n\n"
                f"RECENT FILINGS:\n{filing_block}\n\n"
                "Produce your PersonaOutput JSON now."
            )
            # P3-prereq-5 WS-C: investor-profile context (no-op when unset).
            # Sector-rotation branch is left untouched (profile flows to the
            # non-sector book; sector ETFs reason against macro/sector data).
            user += format_profile_block(state.get("investor_profile"), PERSONA_FRAMING)
            system_prompt = spec.prompt + TERSE_OUTPUT_INSTRUCTION
        # Honor the global default flip in registry (Haiku 4.5) for personas
        # whose spec.default_model wasn't given a per-spec override. Persona
        # specs hardcode "openrouter:qwen/qwen3.6-27b" historically; treat
        # that as "use the global default".
        spec_default = tuple(spec.default_model.split(":", 1))
        fallback = (
            DEFAULT_MODELS.get(name)
            or DEFAULT_MODELS.get("buffett")  # all personas share _DEFAULT
            or spec_default
        )
        if spec_default == ("openrouter", "qwen/qwen3.6-27b"):
            spec_default = fallback
        provider, model = pick_model(state, name, spec_default)
        client = get_llm(provider)
        from apps.backtests.cache import make_cache_ctx
        parsed, resp = call_structured(
            client,
            model=model,
            schema=PersonaOutput,
            messages=[Message("system", system_prompt), Message("user", user)],
            max_tokens=8192,
            temperature=0.4,
            cache_ctx=make_cache_ctx(state, name),
        )
        record_llm_call(
            run_id=state.get("run_id"), backtest_id=state.get("backtest_id"),
            portfolio_target_id=state.get("portfolio_target_id"),
            agent_name=name, resp=resp,
        )
        return {name: parsed.model_dump()}  # type: ignore[return-value]

    node.__name__ = f"run_{name}"
    return node


def all_persona_names() -> list[str]:
    return [n for n, s in AGENT_VERSIONS.items() if s.config.get("kind") == "persona"]
