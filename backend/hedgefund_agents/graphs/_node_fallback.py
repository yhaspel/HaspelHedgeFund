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

from django.conf import settings

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


def _record_failed_attempts(exc: BaseException, state: dict, state_key: str) -> None:
    """Persist the billed-but-unparseable attempts behind a node failure.

    Only ``StructuredOutputError`` carries them (llm/structured.py). Any
    bookkeeping error is swallowed — except ``BudgetExceeded``, which means the
    newly-recorded spend crossed the run's cap and must stop the run.
    """
    from ..llm.structured import StructuredOutputError

    if not isinstance(exc, StructuredOutputError):
        return
    from apps.backtests.exceptions import BudgetExceeded

    from .._persist import record_llm_call

    try:
        record_llm_call(
            run_id=state.get("run_id"),
            backtest_id=state.get("backtest_id"),
            portfolio_target_id=state.get("portfolio_target_id"),
            agent_name=state_key,
            resp=exc.response,
        )
    except BudgetExceeded:
        raise
    except Exception:  # pragma: no cover — bookkeeping must not mask the failure
        log.exception("failed to record billed attempts for agent %r", state_key)


def wrap_backtest_tolerant(
    node_fn: Callable[[dict], dict],
    state_key: str,
) -> Callable[[dict], dict]:
    """Wrap a LangGraph node so a single agent's failure yields a null-signal
    state update for `state_key` instead of aborting the whole graph.

    This has always protected backtests. As the last layer of run self-healing
    (settings.RUN_SELF_HEAL, default True) it now ALSO protects live runs: after
    the adapter's L2 model-fallback has had its chance, a residual unrecoverable
    failure (e.g. a model that returns valid-but-unparseable output 3× and trips
    call_structured's ValueError) degrades just that one agent — the council
    proceeds on the signals that landed and the run completes "done" rather than
    FAILED. ModelUnavailable still re-raises in BOTH contexts: it means the L2
    last-resort model is unreachable too (a genuinely broken account/config),
    which a null signal would mask as all-hold garbage."""
    def wrapped(state: dict) -> dict:
        is_backtest = bool(state.get("backtest_id"))
        tolerant = is_backtest or getattr(settings, "RUN_SELF_HEAL", True)
        if not tolerant:
            return node_fn(state)
        try:
            return node_fn(state)
        except Exception as exc:
            # Config/availability errors (out-of-credits, bad key, exhausted
            # free-tier rate-limit pool) recur on every call, so a null signal
            # here would mask them and let the run "complete" with all-hold
            # garbage. Re-raise so the operator sees the actionable upstream
            # message. (RateLimited is a ModelUnavailable subclass.)
            # BudgetExceeded (P5-SH WS1.2) is the same kind of hard stop: the
            # run has crossed its LLM-spend cap, and null-signalling would let it
            # keep spending past the cap — so it must propagate, not degrade.
            from apps.backtests.exceptions import BudgetExceeded, ModelUnavailable
            if isinstance(exc, ModelUnavailable | BudgetExceeded):
                raise
            # The retry chain in call_structured billed for every attempt even
            # though none parsed. The node never reached its own
            # record_llm_call, so persist the spend here — otherwise the run
            # burns real money that never reaches Run.total_cost_usd or the
            # budget guard. Recording can itself raise BudgetExceeded, which is
            # the correct outcome (the cap really was crossed).
            _record_failed_attempts(exc, state, state_key)
            fb = _fallback_for(state_key)
            if fb is None:
                # No fallback registered: don't swallow silently.
                raise
            log.warning(
                "%s agent %r failed; emitting null signal (self-heal): %s",
                "backtest" if is_backtest else "live", state_key, exc,
            )
            # Mark the degradation (live only) so _persist_outputs can flag the
            # AgentMessage — self-healing must be visible, not silent. Backtests
            # keep their existing null-signal shape byte-identical (they account
            # for degradation via prime_min_completeness, not this marker).
            if not is_backtest:
                fb["_degraded"] = True
                # An UNEXPECTED failure (not the LLM/parse errors the node
                # itself knows how to absorb) must be named on the transcript,
                # not filed under a generic "degraded". _persist_outputs turns
                # this into AgentMessage.status == "error".
                fb["_error"] = type(exc).__name__
            # Stamp the ticker so the null-signal dict is self-identifying
            # for downstream readers that key off it.
            ticker = state.get("ticker", "")
            if state_key in {"news_digest", "decision", "cio"} and ticker:
                fb["ticker"] = ticker
            return {state_key: fb}
    wrapped.__name__ = getattr(node_fn, "__name__", state_key)
    return wrapped
