"""Backtest-tolerant agent-node wrapping.

ROOT CAUSE we're addressing here:
LangGraph nodes either return a state delta or raise. The default semantics
make a single agent failure abort the entire `graph.invoke` call — so for a
N-agent council, even a low per-agent failure rate p compounds to a
ticker-day failure rate of ``1 - (1 - p)^N``:

| per-agent p | N=10 ticker-day failure rate |
|-------------|------------------------------|
| 1%          |  9.6%                        |
| 5%          | 40.1%                        |
| 10%         | 65.1%                        |
| 15%         | 80.3%                        |

For a backtest (which calls graph.invoke for *every* (ticker, day) over a
multi-month master window), this is fatal: even a typical 5% per-agent
LLM failure rate puts ~40% of ticker-days into the failed bucket, and
``prime_min_completeness`` trips and aborts the run.

The fix: in backtest context only, swap each agent node with a wrapper
that catches the node's exception and returns a *null-signal* state
update for its output key. The graph proceeds; downstream PM aggregation
operates on whichever signals successfully landed. Live runs are
unchanged — failures still raise so the operator sees them.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

# Per-output-key fallback dicts. Each one must satisfy the schema in
# `hedgefund_agents/outputs.py`. Kept inline (not Pydantic instances) because
# downstream readers use .get() on plain dicts — same shape as cached entries.
_FALLBACKS: dict[str, dict[str, Any]] = {
    "fundamentals": {
        "revenue_cagr_3y": 0.0, "gross_margin": 0.0, "operating_margin": 0.0,
        "fcf_margin": 0.0, "roic": 0.0, "debt_to_equity": 0.0,
        "quality_score": 0, "notes": "fallback: fundamentals LLM call failed",
    },
    "technicals": {
        "regime": "range", "momentum_1m": 0.0, "momentum_3m": 0.0,
        "momentum_6m": 0.0, "rsi_14": 50.0, "atr_pct": 0.0,
        "signal": "neutral", "confidence": 0,
    },
    "valuation": {
        "dcf_fair_value": None, "multiples_fair_value": None,
        "residual_income_fair_value": None,
        "fair_value_low": 0.0, "fair_value_high": 0.0, "current_price": 0.0,
        "upside_pct": 0.0, "most_sensitive_assumption": "fallback: valuation failed",
    },
    "sentiment": {"score": 0.0, "top_drivers": []},
    "macro": {
        "as_of_date": "1970-01-01",
        "growth_quadrant": "expansion", "inflation_regime": "moderate",
        "yield_curve_state": "normal", "policy_stance": "neutral",
        "narrative": "fallback: macro LLM call failed",
        "sector_implications": {},
    },
    "news_digest": {
        "ticker": "", "digest": "fallback: news LLM call failed",
        "material_events": [], "risk_factor_highlights": [],
        "sentiment_score": 0.0, "sentiment_drivers": [],
    },
    # Personas share the same shape (PersonaOutput). Lookup uses the persona's
    # node name (buffett/munger/etc.); _persona_fallback() returns a copy.
    "_persona": {
        "signal": "neutral", "confidence": 0,
        "thesis": "fallback: persona LLM call failed",
        "key_risks": [],
        "intrinsic_value_estimate": None, "margin_of_safety_pct": None,
    },
    "risk": {
        "hard_caps_applied": ["fallback"],
        "max_position_pct_for_this_trade": 0.0,
        "stop_loss_pct": None, "veto": True,
        "rationale": "fallback: risk LLM call failed; vetoing as conservative default",
    },
    # PM and CIO both write to "decision" (PortfolioOutput shape from the PM,
    # then CIO ratifies/overrides and re-emits the same key). On failure, both
    # collapse to a benign "hold" — same as a council that explicitly chose
    # to do nothing.
    "decision": {
        "ticker": "", "action": "hold", "target_quantity": 0.0,
        "target_weight_pct": 0.0, "aggregate_confidence": 0,
        "rationale": "fallback: LLM call failed; defaulting to hold",
        "dissenting_personas": [],
    },
    "cio": {
        "ticker": "", "action": "hold", "target_weight_pct": 0.0,
        "target_quantity": 0.0, "stop_loss_pct": None,
        "overrode_pm": False, "override_reason": "",
        "outlook": "fallback: CIO LLM call failed",
        "confidence": 0,
    },
}

_PERSONA_NAMES = frozenset({
    "buffett", "munger", "graham", "wood",
    "druckenmiller", "burry", "damodaran", "lynch",
})


def _fallback_for(state_key: str) -> dict[str, Any] | None:
    """Return a shallow-copy null-signal dict for `state_key`, or None if
    we don't know how to fall back (caller will then re-raise the original)."""
    if state_key in _PERSONA_NAMES:
        return dict(_FALLBACKS["_persona"])
    template = _FALLBACKS.get(state_key)
    return dict(template) if template is not None else None


def wrap_backtest_tolerant(
    node_fn: Callable[[dict], dict],
    state_key: str,
) -> Callable[[dict], dict]:
    """Wrap a LangGraph node so that, in backtest context, a node exception
    yields a null-signal state update for `state_key` instead of aborting the
    whole graph. Live runs (state.backtest_id is None) keep the original
    raising behavior so operators still see failures."""
    def wrapped(state: dict) -> dict:
        if not state.get("backtest_id"):
            return node_fn(state)
        try:
            return node_fn(state)
        except Exception as exc:
            fb = _fallback_for(state_key)
            if fb is None:
                # No fallback registered: don't swallow silently.
                raise
            log.warning(
                "backtest agent %r failed; emitting null signal: %s",
                state_key, exc,
            )
            # Stamp the ticker so the null-signal dict is self-identifying
            # for downstream readers that key off it.
            ticker = state.get("ticker", "")
            if state_key in {"news_digest", "decision", "cio"} and ticker:
                fb["ticker"] = ticker
            return {state_key: fb}
    wrapped.__name__ = getattr(node_fn, "__name__", state_key)
    return wrapped
