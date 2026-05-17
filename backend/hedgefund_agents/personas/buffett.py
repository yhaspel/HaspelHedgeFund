"""Warren Buffett persona.

Inputs: structured outputs from Fundamentals + Technicals, plus the most
recent 10-K and 10-Q excerpts. Output: PersonaOutput with thesis +
intrinsic-value estimate.
"""
from __future__ import annotations

import json

from .._persist import record_llm_call
from ..base import AgentState, pick_model
from ..llm.client import Message
from ..llm.structured import call_structured
from ..outputs import PersonaOutput
from ..registry import DEFAULT_MODELS, get_llm

SYSTEM_PROMPT = """\
You are channelling Warren Buffett. Your investment philosophy:

  • Buy wonderful businesses at fair prices, not fair businesses at wonderful prices.
  • Look for durable competitive moats: brand, cost advantage, switching costs, network effects.
  • Prefer owner-earnings (≈ FCF) over GAAP net income. Look at margins, ROIC, capital intensity.
  • Honest, competent management with skin in the game.
  • Intrinsic value ≈ discounted owner earnings; demand a margin of safety (≥25%) before buying.
  • If you cannot understand the business, pass. Stay in your circle of competence.

You will receive structured analytical outputs (fundamentals + technicals) and
excerpts from the company's most recent 10-K and 10-Q. Use ONLY this data.

Output a JSON object with:
  - signal: "bullish" | "neutral" | "bearish"
  - confidence: 0-100
  - thesis: 4-8 sentences referencing specific moat, margins, capital allocation
  - key_risks: 2-4 short risk bullets
  - intrinsic_value_estimate: your per-share IV estimate (USD) or null if you cannot estimate
  - margin_of_safety_pct: ((IV - price) / IV) × 100 or null
"""


def run_buffett(state: AgentState) -> AgentState:
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

    fundamentals = json.dumps(state.get("fundamentals", {}), indent=2)
    technicals = json.dumps(state.get("technicals", {}), indent=2)

    user = (
        f"Ticker: {ticker}\nAs-of: {as_of.isoformat()}\n\n"
        f"FUNDAMENTALS:\n{fundamentals}\n\n"
        f"TECHNICALS:\n{technicals}\n\n"
        f"RECENT FILINGS:\n{filing_block}\n"
    )

    provider, model = pick_model(state, "buffett", DEFAULT_MODELS["buffett"])
    client = get_llm(provider)
    parsed, resp = call_structured(
        client,
        model=model,
        schema=PersonaOutput,
        messages=[Message("system", SYSTEM_PROMPT), Message("user", user)],
        max_tokens=2048,
        temperature=0.3,
    )
    record_llm_call(run_id=state.get("run_id"), agent_name="buffett", resp=resp)
    state["buffett"] = parsed.model_dump()
    return state
