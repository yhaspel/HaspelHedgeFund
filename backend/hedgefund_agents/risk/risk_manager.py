"""Real Risk Manager.

Hard limits are rule-based and applied before any LLM call. The LLM
only writes the *narrative* and may suggest a softer cap; it CANNOT
override a hard veto. A stub portfolio is used in P2a (real portfolio
plumbing arrives with paper-trading in P3a).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from .._persist import record_llm_call
from ..base import AgentState, pick_model
from ..investor_profile_block import RISK_MANAGER_FRAMING, format_profile_block
from ..llm.client import Message
from ..llm.structured import call_structured
from ..outputs import RiskOutput
from ..registry import DEFAULT_MODELS, get_llm
from ..versioning import AgentSpec, register


class RiskLimits(BaseModel):
    max_position_pct: float = 0.20
    max_sector_pct: float = 0.40
    max_portfolio_drawdown_pct: float = 0.25
    min_cash_pct: float = 0.05


@dataclass(frozen=True)
class StubPortfolio:
    cash_balance: float = 100_000.0
    total_value: float = 100_000.0
    drawdown_pct: float = 0.0
    positions: dict[str, float] = field(default_factory=dict)  # ticker -> value
    sector_exposure: dict[str, float] = field(default_factory=dict)  # sector -> value
    sector: str = "Unknown"

    def cash_pct(self) -> float:
        return self.cash_balance / self.total_value if self.total_value else 0.0

    def position_pct(self, ticker: str) -> float:
        return self.positions.get(ticker, 0.0) / self.total_value if self.total_value else 0.0

    def sector_pct(self, sector: str) -> float:
        return self.sector_exposure.get(sector, 0.0) / self.total_value if self.total_value else 0.0


SPEC = AgentSpec(
    agent_name="risk_manager",
    version="v1",
    default_model="openrouter:qwen/qwen3.6-27b",
    prompt="(risk narrative — see risk/risk_manager.py)",
    config={"kind": "risk"},
)
register(SPEC)


def apply_hard_caps(
    portfolio: StubPortfolio,
    limits: RiskLimits,
    ticker: str,
) -> tuple[list[str], float, bool]:
    """Pure function — returns (triggered_caps, max_position_pct_for_trade, veto)."""
    triggered: list[str] = []
    cap = limits.max_position_pct
    veto = False

    if portfolio.drawdown_pct >= limits.max_portfolio_drawdown_pct:
        triggered.append("max_portfolio_drawdown_pct")
        veto = True

    if portfolio.cash_pct() <= limits.min_cash_pct:
        triggered.append("min_cash_pct")
        # Not necessarily a veto; size will be tiny.
        cap = min(cap, max(portfolio.cash_pct() - 0.01, 0.0))

    pos = portfolio.position_pct(ticker)
    headroom = max(limits.max_position_pct - pos, 0.0)
    if headroom < cap:
        triggered.append("max_position_pct")
        cap = headroom

    sector_pct = portfolio.sector_pct(portfolio.sector)
    sector_head = max(limits.max_sector_pct - sector_pct, 0.0)
    if sector_head < cap:
        triggered.append("max_sector_pct")
        cap = sector_head

    if cap <= 0.0:
        veto = True
        cap = 0.0
    return triggered, cap, veto


def get_portfolio(state: AgentState) -> StubPortfolio:
    """Stub portfolio for P2a. Override by setting state["portfolio"]."""
    return state.get("portfolio") or StubPortfolio()  # type: ignore[return-value]


def run_risk_manager(state: AgentState) -> AgentState:
    ticker = state["ticker"]
    portfolio = get_portfolio(state)
    limits = state.get("risk_limits") or RiskLimits()  # type: ignore[assignment]

    triggered, cap, veto = apply_hard_caps(portfolio, limits, ticker)

    # Short-side borrow veto (P2e): set in state by the long-short cycle.
    borrow_veto = bool(state.get("borrow_veto"))  # type: ignore[arg-type]
    if borrow_veto:
        triggered = list(triggered) + ["borrow_not_locatable"]

    # P2m: deterministic Markov persistence check (no LLM cost). Surfaces as
    # context for the LLM narrative and as a structured field on the output.
    from .tools.markov_persistence import markov_persistence_change
    try:
        markov_check = markov_persistence_change(
            ticker="SPY", as_of=state.get("as_of_date"),
        )
    except Exception:  # pragma: no cover - defensive guard
        markov_check = {"status": "unavailable", "reason": "exception"}
    if markov_check.get("risk_flag"):
        triggered = list(triggered) + ["markov_persistence_shift"]

    # LLM narrative (pure text). Pass the deterministic result so it can't
    # contradict the rules.
    default = DEFAULT_MODELS.get("risk_manager", ("openrouter", "qwen/qwen3.6-27b"))
    provider, model = pick_model(state, "risk_manager", default)
    client = get_llm(provider, state=state)
    system = (
        "You are a risk manager. The hard caps have already been computed by deterministic "
        "rules — your job is to write the narrative and optionally suggest a tighter cap "
        "or a stop-loss percentage. You MUST NOT relax the cap or override the veto. "
        "Return a RiskOutput JSON: copy hard_caps_applied, max_position_pct_for_this_trade, "
        "and veto verbatim; you may set stop_loss_pct and write rationale."
    )
    technicals = state.get("technicals", {})
    user = (
        f"Ticker: {ticker}\n"
        f"Computed hard_caps_applied: {triggered}\n"
        f"Computed max_position_pct_for_this_trade: {cap:.4f}\n"
        f"Computed veto: {veto}\n"
        f"Portfolio cash_pct: {portfolio.cash_pct():.4f}\n"
        f"Portfolio drawdown_pct: {portfolio.drawdown_pct:.4f}\n"
        f"Current position_pct: {portfolio.position_pct(ticker):.4f}\n"
        f"Sector exposure_pct ({portfolio.sector}): {portfolio.sector_pct(portfolio.sector):.4f}\n"
        f"Recent volatility (atr_pct): {technicals.get('atr_pct', 0.0):.2f}\n"
        f"Markov persistence check (SPY): {markov_check}\n"
    )
    # P3-prereq-5 WS-G / R17: narrative-only profile injection. apply_hard_caps
    # and the post-call min/OR backstops below ensure the LLM can never relax
    # a cap, flip the veto, or move a number — only the wording shifts.
    user += format_profile_block(state.get("investor_profile"), RISK_MANAGER_FRAMING)
    from apps.backtests.cache import make_cache_ctx
    parsed, resp = call_structured(
        client,
        model=model,
        schema=RiskOutput,
        messages=[Message("system", system), Message("user", user)],
        max_tokens=1024,
        cache_ctx=make_cache_ctx(state, "risk_manager"),
    )
    record_llm_call(
        run_id=state.get("run_id"), backtest_id=state.get("backtest_id"),
            portfolio_target_id=state.get("portfolio_target_id"),
        agent_name="risk_manager", resp=resp,
    )
    out = parsed.model_dump()
    # Enforce: rules win over the LLM.
    out["hard_caps_applied"] = triggered
    out["max_position_pct_for_this_trade"] = min(
        out.get("max_position_pct_for_this_trade", cap), cap
    )
    out["veto"] = bool(out.get("veto")) or veto
    out["borrow_veto"] = borrow_veto
    out["markov_persistence_check"] = markov_check
    return {"risk": out}  # type: ignore[return-value]
