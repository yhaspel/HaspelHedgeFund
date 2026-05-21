"""Pairs-trading council (P2k).

Each candidate pair is debated by N personas (default trio: Druckenmiller,
Burry, Lynch — the macro/contrarian/story slice best suited to a
"divergence: noise or news?" question). One structured LLM call per
(persona × pair). A deterministic Portfolio-Manager-style aggregator
collapses the per-persona votes into a single PairAggregateDecision.

The council is opt-in (PortfolioStrategy.enable_pair_council). When off,
the cycle runs the existing fully-deterministic screener path.
"""
from __future__ import annotations

import logging
from datetime import date as date_cls

from pydantic import BaseModel, Field

from ._persist import record_llm_call
from .llm.client import Message
from .llm.structured import call_structured
from .registry import DEFAULT_MODELS, get_llm

log = logging.getLogger(__name__)


DEFAULT_PAIR_PERSONAS = ["druckenmiller", "burry", "lynch"]


# ---------- Schemas ------------------------------------------------------

class PairPersonaVote(BaseModel):
    """One persona's verdict on a single pair."""
    persona: str
    action: str = Field(description="'enter' or 'skip'")
    confidence: float = Field(ge=0.0, le=1.0)
    thesis: str = Field(description="One-paragraph rationale")
    structural_break_risks: list[str] = Field(default_factory=list)


class PairAggregateDecision(BaseModel):
    """Council-aggregated verdict for one pair."""
    action: str                            # 'enter' or 'skip'
    aggregate_confidence: float            # 0..1
    thesis: str                            # rolled-up paragraph from votes
    votes: list[PairPersonaVote]
    enter_count: int
    skip_count: int


# ---------- Persona prompts ---------------------------------------------

_PERSONA_VOICE: dict[str, str] = {
    "druckenmiller": (
        "You are channelling Stan Druckenmiller. You ask: what is the macro / "
        "regime context, and is one leg of this pair facing a material headwind "
        "the other isn't? Trade noise; skip structural breaks."
    ),
    "burry": (
        "You are channelling Michael Burry. You ask: is the divergence "
        "explained by a specific, identifiable bearish catalyst on one leg "
        "(earnings miss, fraud, secular decline)? If yes, the spread won't "
        "revert — skip. If no, trade."
    ),
    "lynch": (
        "You are channelling Peter Lynch. You ask: are these two companies "
        "still in the same fundamental story / sub-industry, or has one "
        "diverged for a real business reason (product cycle, segment shift)? "
        "Trade if the businesses are still comparable; skip if not."
    ),
    "buffett": (
        "You are channelling Warren Buffett. You ask: are both companies "
        "still durable, high-quality franchises? A spread between two great "
        "businesses is usually noise — trade. A spread because one quietly "
        "lost its moat — skip."
    ),
    "munger": (
        "You are channelling Charlie Munger. You ask: is there something I "
        "would only know by talking to people in the industry? If the answer "
        "is 'this is just price action', trade. If 'something has changed', skip."
    ),
    "graham": (
        "You are channelling Benjamin Graham. You ask: ignoring price, does "
        "the cheaper leg have a wider margin of safety than the more "
        "expensive one? If yes and the businesses are comparable, trade."
    ),
    "wood": (
        "You are channelling Cathie Wood. You ask: is the diverging leg "
        "actually pulling ahead because of a structural innovation (better "
        "tech, faster compounding)? If yes, the spread is the new normal — "
        "skip. If divergence is just rotation, trade."
    ),
    "damodaran": (
        "You are channelling Aswath Damodaran. You ask: do the two firms "
        "still have comparable risk + growth + reinvestment profiles? If yes, "
        "trade. If one's fundamentals have re-rated materially, skip."
    ),
}


_SYSTEM_TEMPLATE = """\
{voice}

You will be asked about a pair of stocks in the same sector. One stock has
recently moved against the other so far that, statistically, the gap is
unusual. The trader's plan is to bet the gap closes again — buy the leg
that lagged and short-sell the leg that rallied.

Your one job is to decide:
  • "enter" — the divergence is noise / temporary; the trade is sensible.
  • "skip"  — the divergence is real / structural; the gap won't close
              soon, the trade is a trap.

Output a JSON object matching PairPersonaVote. Confidence 0..1.
Keep the thesis to one short paragraph (≤ 80 words).
"""


_USER_TEMPLATE = """\
PAIR: long {leg_a} / short {leg_b} (sector: {sector})
As-of date: {as_of}

WHAT THE SCREENER FOUND:
  • The two stocks have moved together historically (correlation {corr:.2f}).
  • Right now the gap between them is unusually wide
    (about {abs_z:.1f}× its normal range).
  • Statistical hint that the gap tends to close: {p_value:.2f}
    (lower = more evidence the gap reverts).

RECENT NEWS / EARNINGS:
{news_block}

Decide: enter or skip. Output your PairPersonaVote JSON now.
"""


def _news_block_for(legs: list[str], news_by_ticker: dict[str, list[str]]) -> str:
    if not news_by_ticker:
        return "(no recent headlines available)"
    lines: list[str] = []
    for leg in legs:
        items = news_by_ticker.get(leg) or []
        if items:
            lines.append(f"  {leg}:")
            lines.extend(f"    - {h}" for h in items[:4])
        else:
            lines.append(f"  {leg}: (no notable headlines)")
    return "\n".join(lines)


# ---------- Aggregator ---------------------------------------------------

def _aggregate(votes: list[PairPersonaVote]) -> PairAggregateDecision:
    if not votes:
        return PairAggregateDecision(
            action="skip", aggregate_confidence=0.0,
            thesis="No persona votes collected; defaulting to skip.",
            votes=[], enter_count=0, skip_count=0,
        )
    enter_w = sum(v.confidence for v in votes if v.action == "enter")
    skip_w = sum(v.confidence for v in votes if v.action == "skip")
    enter_n = sum(1 for v in votes if v.action == "enter")
    skip_n = sum(1 for v in votes if v.action == "skip")
    total_w = enter_w + skip_w
    action = "enter" if enter_w > skip_w else "skip"
    agg_conf = (enter_w if action == "enter" else skip_w) / total_w if total_w > 0 else 0.0
    winners = [v for v in votes if v.action == action]
    thesis_bits = [f"[{v.persona}] {v.thesis.strip()}" for v in winners[:3]]
    rolled = " // ".join(thesis_bits) or "Council split; no clear majority thesis."
    return PairAggregateDecision(
        action=action,
        aggregate_confidence=round(agg_conf, 3),
        thesis=rolled[:1500],
        votes=votes,
        enter_count=enter_n,
        skip_count=skip_n,
    )


# ---------- Public entrypoint -------------------------------------------

def debate_pair(
    *,
    leg_a: str,
    leg_b: str,
    sector: str,
    as_of: date_cls,
    z_current: float,
    correlation: float,
    p_value: float,
    personas: list[str],
    model_overrides: dict[str, str] | None = None,
    user_id: int | None = None,
    portfolio_target_id: int | None = None,
    news_by_ticker: dict[str, list[str]] | None = None,
) -> PairAggregateDecision:
    """Run a per-persona LLM debate over one pair candidate."""
    personas = [p for p in personas if p in _PERSONA_VOICE] or DEFAULT_PAIR_PERSONAS
    news_block = _news_block_for([leg_a, leg_b], news_by_ticker or {})

    user_msg = _USER_TEMPLATE.format(
        leg_a=leg_a, leg_b=leg_b, sector=(sector or "n/a"),
        as_of=as_of.isoformat(),
        corr=correlation, abs_z=abs(z_current),
        p_value=p_value, news_block=news_block,
    )

    votes: list[PairPersonaVote] = []
    overrides = model_overrides or {}
    for persona_name in personas:
        voice = _PERSONA_VOICE[persona_name]
        system_msg = _SYSTEM_TEMPLATE.format(voice=voice)
        # Resolve model: per-agent override → registry default for that persona.
        override = overrides.get(persona_name)
        if override and ":" in override:
            provider, model = override.split(":", 1)
        else:
            provider, model = DEFAULT_MODELS.get(persona_name) or DEFAULT_MODELS["buffett"]
        client = get_llm(provider, user_id=user_id)
        try:
            parsed, resp = call_structured(
                client, model=model, schema=PairPersonaVote,
                messages=[Message("system", system_msg), Message("user", user_msg)],
                max_tokens=1024, temperature=0.3,
            )
            # Pydantic validators are loose: re-stamp the persona field so the
            # LLM can't mis-name itself.
            parsed = PairPersonaVote(
                persona=persona_name,
                action=("enter" if parsed.action.lower().startswith("enter") else "skip"),
                confidence=max(0.0, min(1.0, float(parsed.confidence))),
                thesis=parsed.thesis,
                structural_break_risks=list(parsed.structural_break_risks or []),
            )
            votes.append(parsed)
            record_llm_call(
                run_id=None, backtest_id=None,
                portfolio_target_id=portfolio_target_id,
                agent_name=f"pair_council:{persona_name}", resp=resp,
            )
        except Exception as exc:
            log.warning(
                "pair council vote failed (persona=%s pair=%s/%s): %s",
                persona_name, leg_a, leg_b, exc,
            )
            # A failed persona just abstains — others can still produce a verdict.
            continue
    return _aggregate(votes)
