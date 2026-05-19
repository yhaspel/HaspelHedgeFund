"""Pydantic schemas for every agent's structured output.

These are the *contract* between agents and the rest of the system: the
LLM is free to choose words, but it MUST hand back one of these shapes,
or `call_structured` retries / fails. Downstream code (PM, UI) reads
typed fields, never raw text.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Signal = Literal["bullish", "neutral", "bearish"]
Regime = Literal["trending_up", "trending_down", "range", "breakout", "breakdown"]

# Decision semantics — frozen contract. Downstream code (rebalancer,
# backtest engine, paper-trading adapters) reads this enum and MUST honor
# the meanings below:
#
#   "buy"         — target positive (long) exposure. Sizing comes from
#                   PortfolioOutput.target_weight_pct under the Risk
#                   Manager's per-trade cap.
#   "sell"        — close/exit existing LONG exposure. Does NOT initiate a
#                   short; for that use "open_short". On an absent position
#                   this is effectively a no-op (and rebalancers MUST treat
#                   it as such — never as "open_short" by accident).
#   "hold"        — no new recommendation this cycle. The caller decides
#                   whether that means "keep the existing position" (the
#                   default for paper/live trading) or "target zero" (e.g.
#                   when a strategy is being unwound). Backtest replays
#                   keep existing.
#   "open_short"  — initiate a short position. Reserved for P2e+; the
#                   single-ticker council in P2a/b should not emit this
#                   unless the strategy explicitly enables short_side.
#   "cover_short" — close/exit existing SHORT exposure. Reserved for P2e+.
#
# Adding a new value here is a breaking schema change; bump the Decision
# model's `action` semantics in lockstep with rebalance + broker adapters.
Action = Literal["buy", "hold", "sell", "open_short", "cover_short"]


class FundamentalsOutput(BaseModel):
    revenue_cagr_3y: float
    gross_margin: float
    operating_margin: float
    fcf_margin: float
    roic: float
    debt_to_equity: float
    quality_score: int = Field(ge=0, le=100)
    notes: str


class TechnicalsOutput(BaseModel):
    regime: Regime
    momentum_1m: float
    momentum_3m: float
    momentum_6m: float
    rsi_14: float
    atr_pct: float
    signal: Signal
    confidence: int = Field(ge=0, le=100)


class PersonaOutput(BaseModel):
    signal: Signal
    confidence: int = Field(ge=0, le=100)
    thesis: str
    key_risks: list[str]
    intrinsic_value_estimate: float | None = None
    margin_of_safety_pct: float | None = None


class PMDecision(BaseModel):
    ticker: str
    action: Action
    confidence: int = Field(ge=0, le=100)
    rationale: str
    dissenting_views: list[str] = Field(default_factory=list)


class ValuationOutput(BaseModel):
    dcf_fair_value: float | None = None
    multiples_fair_value: float | None = None
    residual_income_fair_value: float | None = None
    fair_value_low: float
    fair_value_high: float
    current_price: float
    upside_pct: float
    most_sensitive_assumption: str


class SentimentOutput(BaseModel):
    score: float = Field(ge=-1.0, le=1.0)
    top_drivers: list[str] = Field(default_factory=list)


GrowthQuadrant = Literal["expansion", "slowdown", "recession", "recovery"]
InflationRegime = Literal["low", "moderate", "high", "accelerating"]
YieldCurveState = Literal["normal", "flat", "inverted"]
PolicyStance = Literal["tightening", "neutral", "easing"]
SectorTilt = Literal["overweight", "neutral", "underweight"]


class MacroOutput(BaseModel):
    as_of_date: str  # ISO date — kept as string for trivial JSON serialization
    growth_quadrant: GrowthQuadrant
    inflation_regime: InflationRegime
    yield_curve_state: YieldCurveState
    policy_stance: PolicyStance
    narrative: str
    sector_implications: dict[str, SectorTilt] = Field(default_factory=dict)


class MaterialEvent(BaseModel):
    date: str
    headline: str
    tag: str
    materiality: float = Field(ge=0.0, le=10.0)
    url: str = ""


class NewsOutput(BaseModel):
    ticker: str
    digest: str
    material_events: list[MaterialEvent] = Field(default_factory=list)
    risk_factor_highlights: list[str] = Field(default_factory=list)
    sentiment_score: float = Field(ge=-1.0, le=1.0)
    sentiment_drivers: list[str] = Field(default_factory=list)




class RiskOutput(BaseModel):
    hard_caps_applied: list[str] = Field(default_factory=list)
    max_position_pct_for_this_trade: float = Field(ge=0.0, le=1.0)
    stop_loss_pct: float | None = None
    veto: bool = False
    rationale: str


class DissentingPersona(BaseModel):
    name: str
    signal: Signal
    confidence: int
    thesis_summary: str


class PortfolioOutput(BaseModel):
    ticker: str
    action: Action
    target_quantity: float = 0.0
    target_weight_pct: float = 0.0
    aggregate_confidence: int = Field(ge=0, le=100)
    rationale: str
    dissenting_personas: list[DissentingPersona] = Field(default_factory=list)


class CioOutput(BaseModel):
    """Chief Investment Officer — LLM discretionary layer above the PM.

    Strongly biased toward the deterministic PM. Allowed to: downsize,
    flip to hold, or attach a stop_loss. Should NOT flip hold→buy or
    sell→buy without strong evidence — log it in `override_reason` if so.
    """
    ticker: str
    action: Action
    target_weight_pct: float = Field(ge=0.0, le=100.0)
    target_quantity: float = 0.0
    stop_loss_pct: float | None = None
    overrode_pm: bool = False
    override_reason: str = ""  # required if overrode_pm=True
    outlook: str
    confidence: int = Field(ge=0, le=100)
