"""Real Portfolio Manager.

Deterministic aggregation of persona signals + risk cap. The PM is pure
Python — no LLM call. The rationale is composed from the inputs so the
decision is fully traceable.

Parameterized via `state["pm_config"]` so backtests can sweep weights,
thresholds, and sizing without forking the aggregator. Defaults match
the previous behavior so production runs are unchanged.
"""
from __future__ import annotations

import math
from typing import Any

from ..base import AgentState
from ..outputs import DissentingPersona, PortfolioOutput
from ..personas import PERSONA_NODES
from ..versioning import AGENT_VERSIONS, AgentSpec, register

SIGNAL_TO_NUM = {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}
SPEC = AgentSpec(
    agent_name="portfolio_manager",
    version="v2",
    default_model="(deterministic — no LLM)",
    prompt="(deterministic aggregator — see portfolio/portfolio_manager.py)",
    config={"kind": "pm"},
)
register(SPEC)

DEFAULTS = {
    "buy_threshold": 0.25,
    "sell_threshold": -0.25,
    "min_confidence": 0.0,        # gate; 0.0 = no gate
    "weights": None,              # None => use quality_score from registry
    "vol_target_annual": None,    # None => legacy confidence×cap sizing
    "max_weight": 1.0,            # hard cap per name
    "vol_lookback_days": 60,
    "vol_floor": 0.05,
}


def _quality(name: str) -> float:
    spec = AGENT_VERSIONS.get(name)
    if not spec:
        return 1.0
    return float(spec.config.get("quality_score", 1.0))


def _resolve_config(pm_config: dict | None) -> dict:
    cfg = dict(DEFAULTS)
    if pm_config:
        cfg.update({k: v for k, v in pm_config.items() if v is not None})
    return cfg


def aggregate_personas(
    persona_outputs: dict[str, dict], weights: dict[str, float] | None = None
) -> tuple[float, int]:
    """Weighted average of persona signals -> (signed_score in [-1,1], avg_confidence 0-100)."""
    total_weight = 0.0
    signed = 0.0
    conf_weighted = 0.0
    for name, out in persona_outputs.items():
        sig = SIGNAL_TO_NUM.get(out.get("signal", "neutral"), 0.0)
        conf = float(out.get("confidence", 0)) / 100.0
        w = weights[name] if (weights and name in weights) else _quality(name)
        signed += sig * conf * w
        conf_weighted += conf * w
        total_weight += w
    if total_weight == 0:
        return 0.0, 0
    return signed / total_weight, int(round(conf_weighted / total_weight * 100))


def map_to_action(
    signed: float,
    buy_thr: float = 0.25,
    sell_thr: float = -0.25,
    *,
    short_side: bool = False,
) -> str:
    """Standard mapping for long candidates. When `short_side=True`, the
    candidate was surfaced by the screener as a short — a sufficiently
    bearish aggregate becomes `open_short` (instead of just `sell`)."""
    if short_side:
        if signed <= sell_thr:
            return "open_short"
        return "hold"
    if signed >= buy_thr:
        return "buy"
    if signed <= sell_thr:
        return "sell"
    return "hold"


def find_dissent(
    persona_outputs: dict[str, dict], aggregate_action: str
) -> list[DissentingPersona]:
    # Map every Action to the persona signal it implies. open_short/sell
    # are both bearish from the council's perspective; cover_short closes
    # a bearish view, so it implies neutral/bullish-leaning context.
    action_to_signal = {
        "buy": "bullish",
        "sell": "bearish",
        "open_short": "bearish",
        "cover_short": "bullish",
        "hold": "neutral",
    }
    aggregate_signal = action_to_signal.get(aggregate_action, "neutral")
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


def realized_vol_annual(returns: list[float]) -> float:
    if len(returns) < 5:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / max(1, len(returns) - 1)
    return math.sqrt(var) * math.sqrt(252)


def compute_target_weight(
    signed: float,
    action: str,
    confidence: int,
    cap: float,
    cfg: dict,
    trailing_returns: list[float] | None,
) -> float:
    """Resolve a target weight in [-max_weight, +max_weight].

    Three modes:
      1. Vol-targeted: weight = vol_target / realized_vol, clipped to max_weight, signed.
      2. Legacy: signed × cap (the previous confidence-times-cap behavior).
      3. Sell: zero (exit).
    """
    max_weight = float(cfg["max_weight"])
    if action == "sell":
        return 0.0
    if action == "hold":
        return 0.0

    if action == "buy":
        direction = 1.0
    elif action == "open_short":
        direction = -1.0
    else:
        direction = 0.0
    vol_target = cfg.get("vol_target_annual")
    if vol_target and trailing_returns:
        vol = realized_vol_annual(trailing_returns)
        vol = max(float(cfg["vol_floor"]), vol)
        raw = float(vol_target) / vol
        weight = min(max_weight, raw) * direction
    else:
        if cap > 0:
            weight = min(cap, abs(signed) * cap) * direction
        else:
            weight = min(max_weight, abs(signed) * max_weight) * direction
    return weight


def aggregate(
    *,
    ticker: str,
    persona_outputs: dict[str, dict],
    risk: dict[str, Any] | None,
    valuation: dict[str, Any] | None,
    pm_config: dict | None = None,
    trailing_returns: list[float] | None = None,
    portfolio_value: float = 100_000.0,
) -> PortfolioOutput:
    """Pure aggregator usable from the LangGraph node OR from the backtest
    sweep (which has cached persona outputs and wants to re-aggregate cheaply)."""
    cfg = _resolve_config(pm_config)
    risk = risk or {}
    valuation = valuation or {}

    signed, agg_conf = aggregate_personas(persona_outputs, weights=cfg["weights"])
    short_side = bool(cfg.get("short_side", False))
    action = map_to_action(
        signed, cfg["buy_threshold"], cfg["sell_threshold"], short_side=short_side
    )

    veto = bool(risk.get("veto"))
    borrow_veto = bool(risk.get("borrow_veto"))
    cap = float(risk.get("max_position_pct_for_this_trade", 0.0))
    if veto:
        action = "hold"
    if borrow_veto and action == "open_short":
        action = "hold"
    if action == "buy" and (agg_conf / 100.0) < float(cfg["min_confidence"]):
        action = "hold"

    target_weight = compute_target_weight(
        signed, action, agg_conf, cap, cfg, trailing_returns
    )
    target_weight = max(-float(cfg["max_weight"]), min(float(cfg["max_weight"]), target_weight))

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
    if cfg.get("vol_target_annual") and trailing_returns:
        rationale_bits.append(
            f"Vol-targeted sizing (target {cfg['vol_target_annual']:.0%})."
        )
    if dissent:
        rationale_bits.append(
            f"Dissenting personas ({len(dissent)}): "
            + ", ".join(d.name for d in dissent) + "."
        )

    return PortfolioOutput(
        ticker=ticker,
        action=action,  # type: ignore[arg-type]
        target_quantity=target_qty,
        target_weight_pct=target_weight * 100.0,
        aggregate_confidence=agg_conf,
        rationale=" ".join(rationale_bits),
        dissenting_personas=dissent,
    )


def run_portfolio_manager(state: AgentState) -> AgentState:
    ticker = state["ticker"]
    persona_outputs: dict[str, dict] = {
        n: state[n]  # type: ignore[literal-required]
        for n in PERSONA_NODES
        if state.get(n)  # type: ignore[arg-type]
    }
    portfolio = state.get("portfolio")
    portfolio_value = getattr(portfolio, "total_value", 100_000.0)
    out = aggregate(
        ticker=ticker,
        persona_outputs=persona_outputs,
        risk=state.get("risk") or {},  # type: ignore[arg-type]
        valuation=state.get("valuation") or {},  # type: ignore[arg-type]
        pm_config=state.get("pm_config"),  # type: ignore[arg-type]
        trailing_returns=state.get("trailing_returns"),  # type: ignore[arg-type]
        portfolio_value=portfolio_value,
    )
    return {"decision": out.model_dump()}  # type: ignore[return-value]
