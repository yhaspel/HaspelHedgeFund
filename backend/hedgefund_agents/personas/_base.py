"""Shared persona scaffolding.

Each persona supplies a system prompt + an `AgentSpec`; this module
turns that into the (1) registered version (2) graph node callable that
reads fundamentals/technicals/valuation/sentiment + filings and writes a
`PersonaOutput` to `state[<name>]`.
"""
from __future__ import annotations

import json
from collections.abc import Callable

from .._persist import record_llm_call
from ..base import AgentState, pick_model
from ..llm.client import Message
from ..llm.structured import call_structured
from ..outputs import PersonaOutput
from ..registry import DEFAULT_MODELS, get_llm
from ..versioning import AGENT_VERSIONS, AgentSpec, register


def make_persona_node(spec: AgentSpec) -> Callable[[AgentState], AgentState]:
    register(spec)
    name = spec.agent_name

    def node(state: AgentState) -> AgentState:
        ticker = state["ticker"]
        as_of = state["as_of_date"]
        filings = state["filings_provider"].get_recent_filings(
            ticker, as_of=as_of, form_types=["10-K", "10-Q"], limit=2
        )
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
            messages=[Message("system", spec.prompt), Message("user", user)],
            max_tokens=8192,
            temperature=0.4,
            cache_ctx=make_cache_ctx(state, name),
        )
        record_llm_call(run_id=state.get("run_id"), backtest_id=state.get("backtest_id"), agent_name=name, resp=resp)
        return {name: parsed.model_dump()}  # type: ignore[return-value]

    node.__name__ = f"run_{name}"
    return node


def all_persona_names() -> list[str]:
    return [n for n, s in AGENT_VERSIONS.items() if s.config.get("kind") == "persona"]
