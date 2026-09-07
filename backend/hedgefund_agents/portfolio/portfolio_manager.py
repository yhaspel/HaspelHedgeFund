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
    "max_weight": 1.0,            # hard cap per name (legacy confidence×cap path)
    # Per-leg cap for the inverse-vol (vol_target) path. Kept SEPARATE from
    # max_weight because the backtest optimizer searches max_weight in
    # [0.03,0.15] — far below a typical inverse-vol weight (vol_target/vol is
    # ~0.3–5×) — so clamping inverse-vol sizing to max_weight pins every leg to
    # the cap and degenerates risk parity into equal-weight-at-cap. The engine's
    # gross-exposure cap then normalizes Σ|w| to the gross budget.
    "vol_target_max_weight": 0.40,
    "vol_lookback_days": 60,
    "vol_floor": 0.05,
    # Sector-rotation v2: when "flavor"=="sector_rotation", screener pick
    # survives unless any persona votes bearish at confidence >= threshold
    # (0..1 fraction) OR risk manager vetoes. Neutral votes do not block.
    "flavor": "",
    "bearish_veto_threshold": 0.70,
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
        # Inverse-vol sizing: cap each leg at vol_target_max_weight (NOT the
        # small searched max_weight) and let the engine's gross cap normalize
        # Σ|w| — otherwise every leg clips to max_weight (equal-weight-at-cap).
        vt_cap = float(cfg.get("vol_target_max_weight", 0.40))
        weight = min(vt_cap, raw) * direction
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
    # A cap the Risk Manager did NOT state is "unconstrained" (compute_target_weight
    # then sizes off cfg["max_weight"]). A cap it explicitly stated as 0 means
    # "no room for this trade" — which used to fall into the very same branch and
    # size the position at the FULL max_weight, so tightening the cap from 1% to
    # 0% jumped the ticket from 0.8% to 80% of NAV.
    cap_stated = risk.get("max_position_pct_for_this_trade") is not None
    cap = float(risk.get("max_position_pct_for_this_trade") or 0.0)
    zero_cap = cap_stated and cap <= 0.0
    if veto or zero_cap:
        action = "hold"
    if borrow_veto and action == "open_short":
        action = "hold"
    if action == "buy" and (agg_conf / 100.0) < float(cfg["min_confidence"]):
        action = "hold"

    # Sector-rotation v2: screener-led / council-as-veto override. The screener
    # already pre-selected this candidate, so the council can only block.
    # Long side  → default buy, vetoed by bearish persona ≥ threshold.
    # Short side → default open_short, vetoed by bullish persona ≥ threshold
    #              (or borrow_veto). Symmetric rule used when an L/S strategy
    #              auto-routes to the sector council because its universe is
    #              ETFs (see tasks.py daily_long_short_cycle).
    if cfg.get("flavor") == "sector_rotation":
        threshold_int = int(round(float(cfg["bearish_veto_threshold"]) * 100))
        opposing = "bullish" if short_side else "bearish"
        blockers = [
            {
                "persona": name,
                "signal": p.get("signal"),
                "confidence": int(p.get("confidence", 0)),
            }
            for name, p in persona_outputs.items()
            if p.get("signal") == opposing
            and int(p.get("confidence", 0)) >= threshold_int
        ]
        blocked = veto or zero_cap or blockers or (short_side and borrow_veto)
        if blocked:
            action = "hold"
        else:
            action = "open_short" if short_side else "buy"

    target_weight = compute_target_weight(
        signed, action, agg_conf, cap, cfg, trailing_returns
    )
    # Clamp to the cap that matches the sizing path: the inverse-vol (vol_target)
    # path uses vol_target_max_weight, the legacy confidence×cap path uses
    # max_weight. Using max_weight for both re-clips inverse-vol sizing back to
    # the searched [0.03,0.15] cap and defeats risk parity (see DEFAULTS).
    clamp_cap = (
        float(cfg.get("vol_target_max_weight", 0.40))
        if cfg.get("vol_target_annual")
        else float(cfg["max_weight"])
    )
    target_weight = max(-clamp_cap, min(clamp_cap, target_weight))

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
    if zero_cap:
        rationale_bits.append(
            "Risk Manager position cap is 0% for this trade: no room to size; "
            "forcing hold with quantity 0."
        )
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


def build_sector_veto_entry(
    ticker: str,
    persona_outputs: dict[str, dict],
    risk: dict[str, Any],
    threshold: float,
    *,
    short_side: bool = False,
) -> dict[str, Any]:
    """Diagnostic record explaining why a screener-picked candidate was kept
    or vetoed by the council. Stored on PortfolioTarget.sector_veto_log.

    For long-side picks the opposing signal is bearish; for short-side picks
    (L/S on an ETF universe), the opposing signal is bullish.
    """
    threshold_int = int(round(threshold * 100))
    opposing = "bullish" if short_side else "bearish"
    blockers = [
        {
            "persona": name,
            "signal": p.get("signal"),
            "confidence": int(p.get("confidence", 0)),
        }
        for name, p in persona_outputs.items()
        if p.get("signal") == opposing
        and int(p.get("confidence", 0)) >= threshold_int
    ]
    rm_veto = bool(risk.get("veto"))
    borrow_veto = bool(risk.get("borrow_veto")) and short_side
    blocked = rm_veto or borrow_veto or bool(blockers)
    kept_action = "short" if short_side else "buy"
    return {
        "ticker": ticker,
        "side": "short" if short_side else "long",
        "decision": "veto" if blocked else kept_action,
        "reasons": blockers,
        "rm_veto": rm_veto,
        "borrow_veto": borrow_veto,
        "threshold_pct": threshold_int,
    }


def run_portfolio_manager(state: AgentState) -> AgentState:
    ticker = state["ticker"]
    persona_outputs: dict[str, dict] = {
        n: state[n]  # type: ignore[literal-required]
        for n in PERSONA_NODES
        if state.get(n)  # type: ignore[arg-type]
    }
    portfolio = state.get("portfolio")
    portfolio_value = getattr(portfolio, "total_value", 100_000.0)
    pm_config = state.get("pm_config") or {}
    out = aggregate(
        ticker=ticker,
        persona_outputs=persona_outputs,
        risk=state.get("risk") or {},  # type: ignore[arg-type]
        valuation=state.get("valuation") or {},  # type: ignore[arg-type]
        pm_config=pm_config,  # type: ignore[arg-type]
        trailing_returns=state.get("trailing_returns"),  # type: ignore[arg-type]
        portfolio_value=portfolio_value,
    )
    update: dict[str, Any] = {"decision": out.model_dump()}
    if pm_config.get("flavor") == "sector_rotation":
        threshold = float(pm_config.get("bearish_veto_threshold", 0.70))
        update["sector_veto_entry"] = build_sector_veto_entry(
            ticker,
            persona_outputs,
            state.get("risk") or {},
            threshold,
            short_side=bool(pm_config.get("short_side", False)),
        )
    return update  # type: ignore[return-value]
