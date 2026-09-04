"""P7 — the non-overridable deterministic risk layer (apps/portfolios).

This is the "pod-grade risk engineering" the research identifies as the most
valuable replicable idea: **code the LLM cannot override**. It sits between the
cycle's terminal ``_finalize_target`` hook and order submission.

Stage A wires the two cap enforcement points (the key fix vs. the autosubmit
gap):

  1. ``apply_caps`` re-applies the constructor's per-name / sector cap math to the
     target weights BEFORE order creation — this covers the demo-broker path,
     which skips ``confirmation.gate``.
  2. ``make_risk_check`` returns the ``risk_check`` callable passed into the
     scheduled gate, so a single order that would push a name past
     ``max_position_pct`` of NAV is rejected at submit time too.

Stage B extends this module with volatility targeting (Σ|wᵢ|·σᵢ), the drawdown
circuit-breaker, the no-trade band / turnover cost gate, and the liquidity floor.
The binding cap math is the pure-Python constructor (``construction``); nothing
here calls an LLM.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from .construction import _apply_per_name_cap, _apply_sector_cap

log = logging.getLogger(__name__)

# §6.1: realized vol over a trailing ~13 weeks (≈65 trading days).
VOL_WINDOW_DAYS = 65

# A small relative tolerance so floating-point cap math doesn't reject an order
# that is exactly at the cap.
_CAP_TOLERANCE = 1.0001
_FALLBACK_PRICE = Decimal("100")


def apply_caps(
    weights: dict[str, float],
    strategy,
    sector_of: dict[str, str] | None = None,
) -> tuple[dict[str, float], list[dict]]:
    """Re-apply the binding per-name + sector caps to (already vol-scaled) target
    weights. Returns ``(capped_weights, notes)`` for the guardrail audit trail.

    Reuses ``construction._apply_per_name_cap`` / ``_apply_sector_cap`` verbatim —
    the same pure-Python math the constructor binds inside the cycle — so caps
    bind identically at construction time and at the bridge.
    """
    if not weights:
        return weights, []
    capped = _apply_per_name_cap(weights, float(strategy.max_position_pct))
    notes: list[dict] = []
    if sector_of:
        capped, notes = _apply_sector_cap(capped, sector_of, float(strategy.max_sector_pct))
    return capped, notes


def book_nav(pf) -> Decimal:
    """Cost-basis NAV proxy for a book: cash + Σ|qty|·avg_cost.

    Deterministic (no market-data dependency), which keeps the submit-time risk
    gate testable without mocking marks. Stage B's vol-target / drawdown layer
    uses ``valuation.value_portfolio`` (marked-to-market) for the equity curve;
    this proxy is only the denominator for the per-name cap guard.
    """
    if pf is None:
        return Decimal("0")
    nav = Decimal(str(pf.cash_balance or 0))
    for pos in pf.positions.all():
        nav += abs(Decimal(str(pos.quantity))) * Decimal(str(pos.avg_cost or 0))
    return nav


def account_nav(account) -> Decimal:
    """``book_nav`` of the account's whole broker book (legacy path)."""
    return book_nav(getattr(account, "portfolio", None))


def _order_price(order, *, user=None) -> Decimal:
    """Best available per-share price for the order's risk valuation.

    A market order carries no ``limit_price``; valuing it at the flat
    ``_FALLBACK_PRICE`` placeholder badly mis-sizes the notional for any
    instrument not priced near it — a 3%-cap ETF order at $28 then reads as
    ~10% and is falsely ``risk_rejected``. Prefer, in order: the explicit
    limit, the price the order was *sized* against on its rebalance row, then
    the live mark — only then the placeholder.
    """
    if order.limit_price is not None:
        return Decimal(str(order.limit_price))
    ro = getattr(order, "rebalance_order", None)
    if ro is not None and ro.quantity and ro.estimated_notional_usd:
        qty = abs(Decimal(str(ro.quantity)))
        if qty > 0:
            return abs(Decimal(str(ro.estimated_notional_usd))) / qty
    if user is not None:
        try:
            from .valuation import get_mark

            mark = get_mark(order.ticker, user=user)
            if mark is not None and mark.price:
                return Decimal(str(mark.price))
        except Exception:  # noqa: BLE001 — pricing is best-effort; fall through
            pass
    return _FALLBACK_PRICE


def make_risk_check(strategy, *, book=None):
    """Build the ``risk_check`` callable the scheduled gate runs per order.

    Re-applies the strategy's ``max_position_pct`` against the strategy's book
    of record: if filling this order would leave the name's |market value| above
    the cap, the order is rejected (non-empty reasons list). This is the second
    enforcement point — distinct from the LLM-side ``apply_hard_caps`` in
    ``hedgefund_agents/risk/`` — so caps bind both inside the cycle and at submit.

    ``book`` is the Portfolio the cap is measured against — the strategy's fund
    SLEEVE on the shared account (P14: cap = % of the sleeve's NAV, the position
    is the sleeve's), else the order's whole account book (legacy).
    """

    max_pos = float(strategy.max_position_pct or 0)

    def _check(order) -> list[str]:
        reasons: list[str] = []
        if max_pos <= 0:
            return reasons
        account = order.broker_account
        pf = book if book is not None else getattr(account, "portfolio", None)
        nav = book_nav(pf)
        if nav <= 0:
            return reasons
        price = _order_price(order, user=getattr(account, "user", None))
        existing = Decimal("0")
        if pf is not None:
            pos = pf.positions.filter(ticker=order.ticker).first()
            if pos is not None:
                existing = Decimal(str(pos.quantity))
        signed_qty = Decimal(str(order.quantity))
        if order.side in ("sell", "short"):
            signed_qty = -signed_qty
        resulting_notional = abs(existing + signed_qty) * price
        cap_notional = Decimal(str(max_pos * _CAP_TOLERANCE)) * nav
        if resulting_notional > cap_notional:
            reasons.append(
                f"{order.ticker} position {resulting_notional:.0f} would exceed "
                f"max_position_pct ({max_pos:.2%} of NAV {nav:.0f})"
            )
        return reasons

    return _check


# ---------------------------------------------------------------------------
# §6.1 — volatility targeting (de-gross only; never levers up).
# ---------------------------------------------------------------------------
def _data_provider(user):
    from apps.data.providers.factory import get_fmp_provider

    return get_fmp_provider(user=user)


def realized_vol(weights: dict[str, float], as_of, user) -> float:
    """v1 realized-vol proxy: Σ|wᵢ|·σᵢ (weighted sum of per-asset annualised
    vols). There is no covariance matrix in the codebase — only diagonal vols —
    so this conservative proxy stands in for a true w′Σw (a follow-on). Cold
    start: ``compute_vol``'s synthetic fallback keeps σᵢ defined from day 1.
    """
    if not weights:
        return 0.0
    from .vol import compute_vols_for

    try:
        vols = compute_vols_for(
            list(weights.keys()),
            as_of,
            window_days=VOL_WINDOW_DAYS,
            data_provider=_data_provider(user),
        )
    except Exception:  # noqa: BLE001 — vol is best-effort; no scale on failure
        log.exception("realized_vol fetch failed")
        return 0.0
    total = 0.0
    for t, w in weights.items():
        vr = vols.get(t)
        if vr is not None:
            total += abs(float(w)) * float(vr.annualised_vol)
    return total


def vol_target_scale(weights: dict[str, float], autopilot, as_of, user) -> tuple[float, dict]:
    """Scale factor ``min(1, target_vol / realized_vol)`` — capped at 1.0, so
    vol-targeting only *de*-grosses in v1. Returns (scale, audit)."""
    target = float(autopilot.target_vol_pct or 0) / 100.0
    rv = realized_vol(weights, as_of, user)
    if target <= 0 or rv <= 0:
        return 1.0, {"vol_target": target, "realized_vol": rv, "scale": 1.0}
    scale = min(1.0, target / rv)
    return scale, {
        "vol_target": round(target, 4),
        "realized_vol": round(rv, 4),
        "scale": round(scale, 4),
    }


# ---------------------------------------------------------------------------
# §6.3 — drawdown circuit-breaker (deterministic; off the broker book's
# real-fill equity curve, never the hypothetical mark).
# ---------------------------------------------------------------------------
def member_book(autopilot):
    """The book whose equity curve this autopilot's breaker watches: the
    strategy's fund sleeve (P14 — its slice of the shared account), else the
    legacy whole account book, else None."""
    from . import sleeves

    book = sleeves.member_book(autopilot.strategy)
    if book is not None:
        return book
    account = autopilot.broker_account
    return getattr(account, "portfolio", None) if account is not None else None


def broker_equity(autopilot) -> Decimal | None:
    """Marked NAV of the autopilot's book of record (real fills — its sleeve on
    the shared fund account, or its own account). None if no book / valuation
    fails."""
    book = member_book(autopilot)
    if book is None:
        return None
    if not (book.cash_balance or 0) and not book.positions.exists():
        return None  # an unfunded sleeve — nothing to measure (never a 0-peak)
    from .valuation import value_portfolio

    try:
        return Decimal(str(value_portfolio(book).total_value))
    except Exception:  # noqa: BLE001 — fall back to the cost-basis proxy
        return book_nav(book)


def evaluate_drawdown(autopilot) -> dict:
    """Update ``peak_equity_usd`` and resolve the state machine from the current
    drawdown-from-peak. Returns an audit dict. Cold start (null peak) seeds
    ``peak = current`` (drawdown 0) so it never fires spuriously. The human
    un-halt paths (autopilot Resume / re-enable, fund resume) rebase the peak
    to current equity before clearing the halt, so this evaluation is fail-safe
    against a FRESH drawdown from the acknowledged level — not an inescapable
    re-halt loop off the stale all-time peak.
    """
    from .models import StrategyAutopilot

    equity = broker_equity(autopilot)
    if equity is None:
        return {"skipped": "no equity"}

    peak = autopilot.peak_equity_usd
    if peak is None or peak <= 0:
        autopilot.peak_equity_usd = equity
        autopilot.save(update_fields=["peak_equity_usd", "updated_at"])
        return {"seeded_peak": str(equity), "drawdown_pct": 0.0, "state": autopilot.state}

    if equity > peak:
        peak = equity
    dd_pct = float((peak - equity) / peak * 100) if peak > 0 else 0.0

    soft = float(autopilot.dd_soft_cut_pct or 0)
    hard = float(autopilot.dd_hard_halt_pct or 0)
    prior_state = autopilot.state
    if hard > 0 and dd_pct >= hard:
        new_state = StrategyAutopilot.STATE_HALTED
    elif soft > 0 and dd_pct >= soft:
        new_state = StrategyAutopilot.STATE_SOFT_CUT
    else:
        new_state = StrategyAutopilot.STATE_ACTIVE

    autopilot.peak_equity_usd = peak
    autopilot.state = new_state
    autopilot.save(update_fields=["peak_equity_usd", "state", "updated_at"])
    return {
        "equity": str(equity),
        "peak": str(peak),
        "drawdown_pct": round(dd_pct, 3),
        "state": new_state,
        "transition": (prior_state != new_state),
        "prior_state": prior_state,
    }
