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

import httpx

from .._persist import record_llm_call
from ..base import AgentState, pick_model
from ..investor_profile_block import CIO_FRAMING, format_profile_block
from ..llm.client import Message
from ..llm.structured import call_structured
from ..outputs import CioOutput
from ..registry import DEFAULT_MODELS, get_llm
from ..versioning import AgentSpec, register

log = logging.getLogger(__name__)

# Failures the CIO absorbs by ratifying the PM ticket, without flagging the
# transcript. LookupError covers UnknownModelPriceError.
_DEGRADABLE_ERRORS = (ValueError, LookupError, httpx.HTTPError, TimeoutError)


def _hard_stops() -> tuple[type[BaseException], ...]:
    """Exceptions no agent node may absorb. The old blanket `except Exception`
    swallowed BudgetExceeded (so the run finished over its spend cap) and
    ModelUnavailable (so a dead key looked like a ratified PM ticket)."""
    from apps.backtests.exceptions import BudgetExceeded, ModelUnavailable

    from ..errors import OfflineLLMViolation

    return (BudgetExceeded, ModelUnavailable, OfflineLLMViolation)


_HARD_STOPS = _hard_stops()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


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
    client = get_llm(provider, state=state)
    user = (
        f"TICKER: {pm.get('ticker')}\n"
        f"AS-OF: {state['as_of_date'].isoformat()}\n\n"
        f"FULL COUNCIL CONTEXT:\n{json.dumps(context, indent=2, default=str)}\n\n"
        "Decide. If you ratify, copy the PM action/qty/weight. If you change "
        "anything, set overrode_pm=True and explain in override_reason."
    )
    user += format_profile_block(state.get("investor_profile"), CIO_FRAMING)
    node_error = ""
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
    except _HARD_STOPS:
        raise
    except Exception as e:
        if not isinstance(e, _DEGRADABLE_ERRORS):
            # Unexpected: name it on the transcript (AgentMessage.status ==
            # "error") instead of passing silently as a clean ratification.
            log.exception("unexpected failure in cio")
            node_error = type(e).__name__
        log.warning("CIO call failed (%s); ratifying PM unchanged", e)
        parsed = CioOutput(
            ticker=str(pm.get("ticker")),
            action=pm.get("action", "hold"),  # type: ignore[arg-type]
            target_weight_pct=_clamp(float(pm.get("target_weight_pct", 0.0)), 0.0, 100.0),
            target_quantity=float(pm.get("target_quantity", 0.0)),
            overrode_pm=False,
            outlook="(CIO unavailable; PM ticket ratified by fallback)",
            confidence=int(_clamp(float(pm.get("aggregate_confidence", 0) or 0), 0, 100)),
        )

    # -----------------------------------------------------------------
    # Risk re-check. The CIO is an LLM and nothing downstream re-validated
    # its ticket, so it could flip a Risk-Manager veto to a buy and size 3×
    # over the hard cap without even setting overrode_pm. The risk layer is
    # not discretionary: enforce it here, on the CIO's output.
    # -----------------------------------------------------------------
    risk = state.get("risk") or {}
    clamps: list[str] = []
    action = parsed.action
    weight = _clamp(float(parsed.target_weight_pct or 0.0), 0.0, 100.0)
    qty = float(parsed.target_quantity or 0.0)
    confidence = int(_clamp(float(parsed.confidence or 0), 0, 100))

    cap_raw = risk.get("max_position_pct_for_this_trade")
    cap_pct = float(cap_raw) * 100.0 if cap_raw is not None else None

    if bool(risk.get("veto")) and action != "hold":
        clamps.append(
            f"Risk Manager veto in effect — CIO {action!r} at "
            f"{weight:.2f}% overridden to hold."
        )
        action, weight, qty = "hold", 0.0, 0.0
    elif cap_pct is not None and weight > cap_pct + 1e-9:
        # Scale the quantity with the weight so the ticket stays self-consistent
        # instead of carrying a phantom size for a clamped weight.
        qty = qty * (cap_pct / weight) if weight > 0 else 0.0
        clamps.append(
            f"CIO weight {weight:.2f}% exceeds the Risk Manager cap "
            f"{cap_pct:.2f}% — clamped."
        )
        weight = cap_pct
        if cap_pct <= 0.0:
            action, qty = "hold", 0.0

    if clamps:
        log.warning("CIO ticket clamped for %s: %s", pm.get("ticker"), " ".join(clamps))

    # Final decision = CIO's call, after the risk clamps; preserve PM ticket.
    final = dict(pm)
    final["action"] = action
    final["target_quantity"] = qty
    final["target_weight_pct"] = weight
    final["rationale"] = (
        f"[PM] {pm.get('rationale', '')}\n"
        f"[CIO] {parsed.outlook}"
        + (f"\n[CIO OVERRIDE] {parsed.override_reason}" if parsed.overrode_pm else "")
        + ("\n[RISK CLAMP] " + " ".join(clamps) if clamps else "")
    )[:4000]

    cio_out = parsed.model_dump()
    cio_out.update({
        "action": action,
        "target_weight_pct": weight,
        "target_quantity": qty,
        "confidence": confidence,
        "risk_clamps": clamps,
    })
    if node_error:
        cio_out["_error"] = node_error
    return {
        "decision": final,
        "pm_decision": pm,
        "cio": cio_out,
    }
