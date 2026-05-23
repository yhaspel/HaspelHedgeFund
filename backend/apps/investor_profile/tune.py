"""Deterministic re-derivation of the agent_brief + recommended strategies
when a user tunes their bands (P3-prereq-5 WS-F).

No LLM call. The brief is rebuilt from the existing structured analysis with
the requested band(s) substituted; the strategy list is regenerated from the
deterministic fallback so it stays in lockstep with the new risk band.

Tuning never touches free-text fields (``behavioral_traits``,
``key_constraints``, ``summary``). Those carry over from the parent
``QuestionnaireResponse`` so a user cannot inject new prose.
"""
from __future__ import annotations

from typing import Iterable

from .analysis import (
    AGENT_BRIEF_MAX_WORDS,
    ProfileAnalysis,
    StrategyRecommendation,
    build_fallback_brief,
    build_fallback_strategies,
)

RISK_BANDS = {
    "conservative",
    "moderate_conservative",
    "moderate",
    "moderate_aggressive",
    "aggressive",
}
HORIZON_BANDS = {"short", "medium", "long"}
PATIENCE_BANDS = {"low", "medium", "high"}

_RISK_LABEL = {
    "conservative": "conservative",
    "moderate_conservative": "moderately conservative",
    "moderate": "moderate",
    "moderate_aggressive": "moderately aggressive",
    "aggressive": "aggressive",
}
_HORIZON_LABEL = {
    "short": "short (under ~3 years)",
    "medium": "medium (3–10 years)",
    "long": "long (10+ years)",
}
_PATIENCE_LABEL = {
    "low": "low",
    "medium": "medium",
    "high": "high",
}


def _compose_brief(
    parent_brief: str,
    risk_band: str,
    horizon_band: str,
    patience_band: str,
    answers: dict,
) -> str:
    """Re-derive the brief deterministically.

    Take the parent brief as a structural template and overwrite the three
    band-bearing sentences. If the parent is missing or the surgery would
    confuse downstream agents, fall back to the deterministic builder.
    """
    bits = [
        "Investor preferences (stated context, not an instruction).",
        f"Risk appetite: {_RISK_LABEL.get(risk_band, risk_band)}.",
        f"Time horizon: {_HORIZON_LABEL.get(horizon_band, horizon_band)}.",
        f"Patience for thesis to play out: {_PATIENCE_LABEL.get(patience_band, patience_band)}.",
    ]
    goal = answers.get("primary_goal")
    if goal:
        bits.append(f"Primary goal: {goal.lower()}.")
    cadence = answers.get("trade_frequency")
    if cadence:
        bits.append(f"Trading cadence: {cadence.lower()}.")
    avoid = answers.get("avoid") or []
    if avoid:
        bits.append("Avoid: " + ", ".join(avoid).lower() + ".")
    sectors = answers.get("preferred_sectors") or []
    if sectors and "No preference" not in sectors:
        bits.append("Preferred themes: " + ", ".join(sectors) + ".")
    text = " ".join(bits)
    words = text.split()
    if len(words) > AGENT_BRIEF_MAX_WORDS:
        text = " ".join(words[:AGENT_BRIEF_MAX_WORDS]).rstrip(",;:.") + "…"
    if parent_brief and not text:
        return parent_brief
    return text


def rederive_for_tune(
    parent_analysis: dict,
    parent_answers: dict,
    *,
    risk_band: str | None = None,
    horizon_band: str | None = None,
    patience_band: str | None = None,
) -> ProfileAnalysis:
    """Return a new ``ProfileAnalysis`` built from the parent with band edits."""
    if risk_band is not None and risk_band not in RISK_BANDS:
        raise ValueError(f"unknown risk_band: {risk_band}")
    if horizon_band is not None and horizon_band not in HORIZON_BANDS:
        raise ValueError(f"unknown horizon_band: {horizon_band}")
    if patience_band is not None and patience_band not in PATIENCE_BANDS:
        raise ValueError(f"unknown patience_band: {patience_band}")

    new_risk = risk_band or parent_analysis.get("risk_band", "moderate")
    new_horizon = horizon_band or parent_analysis.get("horizon_band", "medium")
    new_patience = patience_band or parent_analysis.get("patience_band", "medium")

    brief = _compose_brief(
        parent_brief=parent_analysis.get("agent_brief", "") or "",
        risk_band=new_risk,
        horizon_band=new_horizon,
        patience_band=new_patience,
        answers=parent_answers or {},
    )
    if not brief:
        brief = build_fallback_brief(parent_answers or {})

    recs: list[StrategyRecommendation] = build_fallback_strategies(
        parent_answers or {}, new_risk
    )

    traits: Iterable[str] = parent_analysis.get("behavioral_traits") or []
    constraints: Iterable[str] = parent_analysis.get("key_constraints") or []
    insights: Iterable[str] = parent_analysis.get("insights") or [
        "Fine-tuned bands applied; downstream agents updated."
    ]

    return ProfileAnalysis(
        investor_type=parent_analysis.get("investor_type", "Tuned investor"),
        risk_band=new_risk,  # type: ignore[arg-type]
        horizon_band=new_horizon,  # type: ignore[arg-type]
        patience_band=new_patience,  # type: ignore[arg-type]
        behavioral_traits=list(traits)[:6],
        key_constraints=list(constraints)[:6],
        summary=parent_analysis.get("summary", "")
        or "Profile bands fine-tuned by the investor.",
        insights=list(insights)[:6] or ["Fine-tuned bands applied."],
        recommended_strategies=recs,
        agent_brief=brief,
    )
