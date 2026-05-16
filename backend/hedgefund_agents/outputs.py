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
Action = Literal["buy", "hold", "sell"]


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
