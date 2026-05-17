"""Real Portfolio Manager.

Deterministic aggregation of persona signals + risk cap. The PM is
pure Python — no LLM call. The rationale is composed from the inputs
so the decision is fully traceable.
"""
from __future__ import annotations

from ..base import AgentState
from ..outputs import DissentingPersona, PortfolioOutput
from ..personas import PERSONA_NODES
from ..versioning import AGENT_VERSIONS, AgentSpec, register

SIGNAL_TO_NUM = {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}
NUM_TO_ACTION = {"buy": 1, "hold": 0, "sell": -1}
SPEC = AgentSpec(
    agent_name="portfolio_manager",
    version="v1",
    default_model="(deterministic — no LLM)",
    prompt="(deterministic aggregator — see portfolio/portfolio_manager.py)",
    config={"kind": "pm"},
)
register(SPEC)

BUY_THRESHOLD = 0.25
SELL_THRESHOLD = -0.25


def _quality(name: str) -> float:
    spec = AGENT_VERSIONS.get(name)
    if not spec:
        return 1.0
    return float(spec.config.get("quality_score", 1.0))


def aggregate_personas(persona_outputs: dict[str, dict]) -> tuple[float, int]:
    """Returns (signed_score in [-1,1], aggregate_confidence 0-100)."""
    total_weight = 0.0
    signed = 0.0
    conf_weighted = 0.0
    for name, out in persona_outputs.items():
        sig = SIGNAL_TO_NUM.get(out.get("signal", "neutral"), 0.0)
        conf = float(out.get("confidence", 0)) / 100.0
        w = conf * _quality(name)
        signed += sig * w
        conf_weighted += conf * _quality(name)
        total_weight += _quality(name)
    if total_weight == 0:
        return 0.0, 0
    return signed / total_weight, int(round(conf_weighted / total_weight * 100))


def map_to_action(signed: float) -> str:
    if signed >= BUY_THRESHOLD:
        return "buy"
    if signed <= SELL_THRESHOLD:
        return "sell"
    return "hold"


def find_dissent(
    persona_outputs: dict[str, dict], aggregate_action: str
) -> list[DissentingPersona]:
    aggregate_signal = {"buy": "bullish", "sell": "bearish", "hold": "neutral"}[aggregate_action]
    out = []
    for name, p in persona_outputs.items():
        sig = p.get("signal", "neutral")
        if sig != aggregate_signal:
            out.append(
                DissentingPersona(
                    name=name,
                    signal=sig,  # type: ignore[arg-type]
                    confidence=int(p.get("confidence", 0)),
                    thesis_summary=(p.get("thesis") or "").strip()[:240],
                )
            )
    return out


def run_portfolio_manager(state: AgentState) -> AgentState:
    ticker = state["ticker"]
    persona_outputs: dict[str, dict] = {
        n: state[n]  # type: ignore[literal-required]
        for n in PERSONA_NODES
        if state.get(n)  # type: ignore[arg-type]
    }
    risk = state.get("risk") or {}  # type: ignore[assignment]
    valuation = state.get("valuation") or {}  # type: ignore[assignment]

    signed, agg_conf = aggregate_personas(persona_outputs)
    action = map_to_action(signed)
    veto = bool(risk.get("veto"))
    cap = float(risk.get("max_position_pct_for_this_trade", 0.0))

    if veto:
        action = "hold"

    if action == "buy":
        target_weight = min(cap, max(0.0, signed) * cap)
    elif action == "sell":
        target_weight = 0.0  # exit signal
    else:
        target_weight = 0.0

    portfolio = state.get("portfolio")  # may be None
    portfolio_value = getattr(portfolio, "total_value", 100_000.0)
    current_price = float(valuation.get("current_price") or 0.0)
    target_dollars = target_weight * portfolio_value
    target_qty = (target_dollars / current_price) if current_price > 0 else 0.0

    dissent = find_dissent(persona_outputs, action)

    rationale_bits = [
        f"Aggregated persona signal: {signed:+.2f} (confidence {agg_conf}).",
        f"Action: {action}.",
    ]
    if veto:
        rationale_bits.append("Risk Manager veto in effect: forcing hold.")
    if risk.get("hard_caps_applied"):
        rationale_bits.append(f"Hard caps applied: {', '.join(risk['hard_caps_applied'])}.")
    if dissent:
        rationale_bits.append(
            f"Dissenting personas ({len(dissent)}): "
            + ", ".join(d.name for d in dissent) + "."
        )

    out = PortfolioOutput(
        ticker=ticker,
        action=action,  # type: ignore[arg-type]
        target_quantity=target_qty,
        target_weight_pct=target_weight * 100.0,
        aggregate_confidence=agg_conf,
        rationale=" ".join(rationale_bits),
        dissenting_personas=dissent,
    )
    return {"decision": out.model_dump()}  # type: ignore[return-value]
