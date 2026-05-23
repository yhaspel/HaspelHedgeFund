"""Chief Investment Officer — LLM discretionary layer above the deterministic PM.

The PM produces an audit-proof aggregate order. The CIO then reads the PM's
ticket + every upstream signal (analytical, personas, risk, news, macro) and
either ratifies the PM's call or adjusts it (downsize, flip to hold, attach a
stop). Bias is strongly toward the PM. Disabled in backtests via
`state["disable_cio"] = True`.
"""
from __future__ import annotations

import json
import logging

from .._persist import record_llm_call
from ..base import AgentState, pick_model
from ..investor_profile_block import CIO_FRAMING, format_profile_block
from ..llm.client import Message
from ..llm.structured import call_structured
from ..outputs import CioOutput
from ..registry import DEFAULT_MODELS, get_llm
from ..versioning import AgentSpec, register

log = logging.getLogger(__name__)

SPEC = AgentSpec(
    agent_name="cio",
    version="v1",
    default_model="openrouter:qwen/qwen3.6-27b",
    prompt=(
        "You are the Chief Investment Officer. A deterministic Portfolio Manager "
        "has already aggregated the council's signals into an order. Your job is "
        "ONE of:\n"
        "  (a) RATIFY the PM's action and size unchanged.\n"
        "  (b) DOWNSIZE: keep the action, reduce target_weight_pct.\n"
        "  (c) FLIP TO HOLD: if a material risk/news/macro signal the PM "
        "      under-weighted is decisive.\n"
        "  (d) ATTACH a stop_loss_pct.\n"
        "You may NOT flip hold→buy or sell→buy without overwhelming evidence; "
        "if you do, you MUST explain in override_reason. Default to (a). "
        "Set overrode_pm=True if you change action OR change target_weight_pct "
        "by more than 25%. Return a CioOutput JSON."
    ),
    config={"kind": "cio"},
)
register(SPEC)


def run_cio(state: AgentState) -> AgentState:
    if state.get("disable_cio"):
        return {}

    pm = state.get("decision") or {}
    if not pm:
        return {}

    context = {
        "pm_ticket": {
            "ticker": pm.get("ticker"),
            "action": pm.get("action"),
            "target_quantity": pm.get("target_quantity"),
            "target_weight_pct": pm.get("target_weight_pct"),
            "aggregate_confidence": pm.get("aggregate_confidence"),
            "rationale": pm.get("rationale"),
            "dissenting_personas": pm.get("dissenting_personas", []),
        },
        "risk": state.get("risk") or {},
        "macro": state.get("macro") or {},
        "news_digest": state.get("news_digest") or {},
        "fundamentals": state.get("fundamentals") or {},
        "technicals": state.get("technicals") or {},
        "valuation": state.get("valuation") or {},
        "sentiment": state.get("sentiment") or {},
    }

    provider, model = pick_model(state, "cio", DEFAULT_MODELS["cio"])
    client = get_llm(provider)
    user = (
        f"TICKER: {pm.get('ticker')}\n"
        f"AS-OF: {state['as_of_date'].isoformat()}\n\n"
        f"FULL COUNCIL CONTEXT:\n{json.dumps(context, indent=2, default=str)}\n\n"
        "Decide. If you ratify, copy the PM action/qty/weight. If you change "
        "anything, set overrode_pm=True and explain in override_reason."
    )
    user += format_profile_block(state.get("investor_profile"), CIO_FRAMING)
    try:
        from apps.backtests.cache import make_cache_ctx
        parsed, resp = call_structured(
            client,
            model=model,
            schema=CioOutput,
            messages=[Message("system", SPEC.prompt), Message("user", user)],
            max_tokens=4096,
            temperature=0.2,
            cache_ctx=make_cache_ctx(state, "cio"),
        )
        record_llm_call(
            run_id=state.get("run_id"), backtest_id=state.get("backtest_id"),
            portfolio_target_id=state.get("portfolio_target_id"),
            agent_name="cio", resp=resp,
        )
    except Exception as e:
        log.warning("CIO call failed (%s); ratifying PM unchanged", e)
        parsed = CioOutput(
            ticker=str(pm.get("ticker")),
            action=pm.get("action", "hold"),  # type: ignore[arg-type]
            target_weight_pct=float(pm.get("target_weight_pct", 0.0)),
            target_quantity=float(pm.get("target_quantity", 0.0)),
            overrode_pm=False,
            outlook="(CIO unavailable; PM ticket ratified by fallback)",
            confidence=int(pm.get("aggregate_confidence", 0)),
        )

    # Final decision = CIO's call; preserve PM ticket for audit.
    final = dict(pm)
    final["action"] = parsed.action
    final["target_quantity"] = parsed.target_quantity
    final["target_weight_pct"] = parsed.target_weight_pct
    final["rationale"] = (
        f"[PM] {pm.get('rationale', '')}\n"
        f"[CIO] {parsed.outlook}"
        + (f"\n[CIO OVERRIDE] {parsed.override_reason}" if parsed.overrode_pm else "")
    )[:4000]

    return {
        "decision": final,
        "pm_decision": pm,
        "cio": parsed.model_dump(),
    }
