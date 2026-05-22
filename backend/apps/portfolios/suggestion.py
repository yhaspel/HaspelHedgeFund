"""P3: Deterministic position-suggestion engine.

``suggest_position(decision, portfolio) -> PositionSuggestion`` returns
both final numbers and a per-factor breakdown so the UI can explain every
digit ("Why this size?"). No LLM calls.

All math runs in **fractions** internally (``0.03`` = 3%); API/UI fields
whose names end in ``_pct`` are serialized in percentage points, matching
the existing ``Decision.target_weight_pct`` convention.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_cls
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from apps.data.models import MacroSnapshot

from .models import Portfolio, PortfolioStrategy, PortfolioTarget, Position
from .quantity_policy import QuantityPolicy, round_quantity_for_open
from .valuation import get_mark, value_portfolio

# Per-position hard cap for the Manual Book.
MANUAL_MAX_POSITION_PCT = Decimal("0.20")
# Confidence fallback ceiling when the run decision has no target_weight.
DEFAULT_BASE_MAX = Decimal("0.05")


@dataclass
class SuggestionFactor:
    key: str
    label: str
    effect: str
    detail: str


@dataclass
class PositionSuggestion:
    ticker: str
    side: str  # "long" | "short"
    suggested_weight_pct: Decimal       # percentage points (3.0 == 3%)
    target_notional_usd: Decimal        # ideal notional before quantity rounding
    target_quantity: Decimal            # ideal quantity before quantity rounding
    suggested_notional_usd: Decimal     # accepted_qty * current_price
    suggested_quantity: Decimal         # after QuantityPolicy
    quantity_mode: str                  # "whole" | "fractional"
    rounding_residual_usd: Decimal
    current_price: Decimal              # latest daily close
    price_as_of: date_cls | None
    portfolio_total_value: Decimal
    free_cash: Decimal
    existing_quantity: Decimal          # signed; 0 if no existing position
    existing_side: str                  # "long" | "short" | "flat"
    action_label: str                   # "Open" | "Increase" | "Reduce/close first"
    factors: list[SuggestionFactor] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _pct(fraction: Decimal) -> Decimal:
    return (fraction * Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP,
    )


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _macro_delta(snapshot: MacroSnapshot | None) -> tuple[Decimal, list[str]]:
    """Sum the four macro axes into a long-side multiplier delta.

    Returns the long-side delta (sign flipped for shorts at the call site)
    plus a list of per-axis chips for the UI breakdown.
    """
    chips: list[str] = []
    if snapshot is None:
        return Decimal("0"), chips
    delta = Decimal("0")
    growth_map = {
        "expansion": Decimal("0.10"),
        "recovery": Decimal("0.05"),
        "slowdown": Decimal("-0.05"),
        "recession": Decimal("-0.15"),
    }
    inflation_map = {
        "low": Decimal("0.03"),
        "moderate": Decimal("0"),
        "high": Decimal("-0.05"),
        "accelerating": Decimal("-0.08"),
    }
    curve_map = {
        "normal": Decimal("0.03"),
        "flat": Decimal("0"),
        "inverted": Decimal("-0.08"),
    }
    policy_map = {
        "easing": Decimal("0.05"),
        "neutral": Decimal("0"),
        "tightening": Decimal("-0.05"),
    }

    for label, value, mapping in (
        ("growth", (snapshot.growth_quadrant or "").lower(), growth_map),
        ("inflation", (snapshot.inflation_regime or "").lower(), inflation_map),
        ("curve", (snapshot.yield_curve_state or "").lower(), curve_map),
        ("policy", (snapshot.policy_stance or "").lower(), policy_map),
    ):
        contribution = mapping.get(value, Decimal("0"))
        delta += contribution
        chips.append(f"{label}={value or 'n/a'}({_pct(contribution)}pp)")

    # Clamp to [-0.30, +0.20] per the phase plan.
    if delta > Decimal("0.20"):
        delta = Decimal("0.20")
    elif delta < Decimal("-0.30"):
        delta = Decimal("-0.30")
    return delta, chips


def _strategy_for_target(target: PortfolioTarget | None) -> PortfolioStrategy | None:
    return target.strategy if target is not None else None


def _existing_position(
    portfolio: Portfolio, ticker: str,
) -> Position | None:
    return portfolio.positions.filter(ticker__iexact=ticker).first()


def suggest_position(
    *,
    decision: Any,
    portfolio: Portfolio,
    quantity_mode: str = "whole",
) -> PositionSuggestion:
    """Derive a default direction + size for entering ``decision.ticker``."""
    factors: list[SuggestionFactor] = []
    warnings: list[str] = []

    ticker = (decision.ticker or "").strip().upper()
    if not ticker:
        raise ValueError("decision has no ticker")

    # Step 1: direction from the decision.
    action = (decision.action or "").lower()
    raw_side = (decision.side or "").lower()
    if action in ("hold",):
        side = raw_side if raw_side in ("long", "short") else "long"
        warnings.append(
            "Council voted hold — entering is not endorsed by the run. "
            "Suggested weight starts at 0."
        )
    elif action in ("sell", "cover_short", "skip"):
        side = raw_side if raw_side in ("long", "short") else "long"
        warnings.append(
            f"Decision is '{action}', not a new entry. Suggested weight is 0; "
            "use Close on the existing position instead."
        )
    elif action == "enter" or raw_side == "pair":
        raise ValueError("pair decisions are not supported for manual entry in v1")
    else:
        if raw_side in ("long", "short"):
            side = raw_side
        elif action == "open_short":
            side = "short"
        else:
            side = "long"  # default for buy / unknown

    # Step 2: base weight from the decision.
    base_weight = Decimal("0")
    base_source = ""
    target_signed = Decimal(str(decision.target_weight_signed or 0))
    target_pct = Decimal(str(decision.target_weight_pct or 0))
    confidence = Decimal(str(decision.confidence or 0))

    if action in ("hold", "sell", "cover_short", "skip"):
        base_weight = Decimal("0")
        base_source = f"decision={action}"
    elif target_signed != 0:
        base_weight = (target_signed.copy_abs() / Decimal("100"))
        base_source = f"target_weight_signed={target_signed}%"
    elif target_pct != 0:
        base_weight = (target_pct.copy_abs() / Decimal("100"))
        base_source = f"target_weight_pct={target_pct}%"
    else:
        base_weight = (confidence / Decimal("100")) * DEFAULT_BASE_MAX
        base_source = f"confidence={confidence} (fallback)"
    factors.append(SuggestionFactor(
        key="run_decision",
        label="Run decision",
        effect=f"base {_pct(base_weight)}%",
        detail=(
            f"{ticker} {side.upper()} {action} · conf {confidence} · "
            f"source: {base_source}"
        ),
    ))

    # Step 3: strategy adjustment (only for strategy-sourced runs).
    run = getattr(decision, "run", None)
    portfolio_target = getattr(run, "portfolio_target", None)
    strategy = _strategy_for_target(portfolio_target)
    adjusted = base_weight
    if strategy is not None and portfolio_target is not None:
        cycle_weights = portfolio_target.target_weights or {}
        cycle_weight = cycle_weights.get(ticker) or cycle_weights.get(ticker.upper())
        max_pos = Decimal(str(strategy.max_position_pct or 0))
        detail_parts: list[str] = []
        if cycle_weight is not None:
            cycle_fraction = Decimal(str(cycle_weight))
            if cycle_fraction != 0:
                # Strategy targets store fractions; honour the sign.
                if (decision.side or "") == "":
                    side = "long" if cycle_fraction > 0 else "short"
                adjusted = cycle_fraction.copy_abs()
                detail_parts.append(
                    f"strategy target {_pct(adjusted)}%"
                )
        if max_pos > 0 and adjusted > max_pos:
            detail_parts.append(
                f"clamped to strategy max_position_pct={_pct(max_pos)}%"
            )
            adjusted = max_pos
        factors.append(SuggestionFactor(
            key="strategy",
            label=f"Strategy: {strategy.name}",
            effect=f"adjusted {_pct(adjusted)}%",
            detail=" · ".join(detail_parts) or "no strategy override",
        ))

    # Step 4: macro multiplier.
    macro_snapshot = MacroSnapshot.objects.order_by("-as_of_date").first()
    macro_delta, macro_chips = _macro_delta(macro_snapshot)
    if side == "short":
        macro_delta = -macro_delta
    macro_multiplier = Decimal("1") + macro_delta
    if macro_snapshot is None:
        macro_multiplier = Decimal("1")
        warnings.append("No macro snapshot available — multiplier defaulted to 1.0.")
        factors.append(SuggestionFactor(
            key="macro",
            label="Macro regime",
            effect="×1.00",
            detail="no MacroSnapshot — defaulted to neutral",
        ))
    else:
        adjusted = adjusted * macro_multiplier
        factors.append(SuggestionFactor(
            key="macro",
            label=f"Macro: {macro_snapshot.growth_quadrant}/{macro_snapshot.inflation_regime}",
            effect=f"×{macro_multiplier:.2f}",
            detail=" · ".join(macro_chips),
        ))

    # Step 5: portfolio caps.
    valuation = value_portfolio(portfolio)
    total_value = valuation.total_value
    free = valuation.free_cash
    if total_value <= 0:
        total_value = Decimal(str(portfolio.cash_balance or Decimal("0")))

    per_position_cap = MANUAL_MAX_POSITION_PCT
    if adjusted > per_position_cap:
        factors.append(SuggestionFactor(
            key="position_cap",
            label="Per-position cap",
            effect=f"capped at {_pct(per_position_cap)}%",
            detail="Hard 20% cap for the Manual Book.",
        ))
        adjusted = per_position_cap

    cash_cap_fraction: Decimal | None = None
    if side == "long" and total_value > 0:
        cash_cap_fraction = (free / total_value)
        if cash_cap_fraction < 0:
            cash_cap_fraction = Decimal("0")
        if adjusted > cash_cap_fraction:
            factors.append(SuggestionFactor(
                key="cash_cap",
                label="Free-cash cap",
                effect=f"capped at {_pct(cash_cap_fraction)}%",
                detail=f"Free cash ${free:.2f} of ${total_value:.2f} book.",
            ))
            adjusted = cash_cap_fraction

    final_weight = max(adjusted, Decimal("0"))
    target_notional = (final_weight * total_value).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP,
    )

    # Step 6: existing-position handling.
    existing = _existing_position(portfolio, ticker)
    if existing is not None:
        existing_qty = existing.quantity
        existing_side = "short" if existing.quantity < 0 else "long"
        if existing_side != side and final_weight > 0:
            action_label = "Reduce/close first"
            warnings.append(
                f"{ticker} already exists on the {existing_side} side. "
                "Reduce or close that position before opening the opposite side."
            )
        else:
            action_label = "Increase position"
    else:
        existing_qty = Decimal("0")
        existing_side = "flat"
        action_label = "Open position"

    # Step 7: quantity policy.
    price_mark = get_mark(ticker, user=portfolio.user)
    if price_mark is None or price_mark.price <= 0:
        warnings.append(
            f"No recent daily close available for {ticker}. "
            "Set entry_price manually."
        )
        current_price = Decimal("0")
        price_as_of: date_cls | None = None
        target_quantity = Decimal("0")
        accepted_quantity = Decimal("0")
        rounded_notional = Decimal("0")
        residual = Decimal("0")
    else:
        current_price = price_mark.price
        price_as_of = price_mark.as_of
        if price_mark.stale:
            warnings.append(
                f"Stale price: last close {price_mark.age_days} days ago."
            )
        target_quantity = (target_notional / current_price) if current_price > 0 else Decimal("0")
        policy = QuantityPolicy.from_mode(quantity_mode)
        result = round_quantity_for_open(target_quantity, current_price, policy)
        accepted_quantity = result.quantity
        rounded_notional = result.rounded_notional
        residual = result.residual_notional
        if result.warning:
            warnings.append(result.warning)

    suggested_weight_pct = _pct(
        (rounded_notional / total_value) if total_value > 0 else Decimal("0")
    )

    return PositionSuggestion(
        ticker=ticker,
        side=side,
        suggested_weight_pct=suggested_weight_pct,
        target_notional_usd=_money(target_notional),
        target_quantity=target_quantity,
        suggested_notional_usd=_money(rounded_notional),
        suggested_quantity=accepted_quantity,
        quantity_mode=quantity_mode,
        rounding_residual_usd=_money(residual),
        current_price=current_price,
        price_as_of=price_as_of,
        portfolio_total_value=_money(total_value),
        free_cash=_money(free),
        existing_quantity=existing_qty,
        existing_side=existing_side,
        action_label=action_label,
        factors=factors,
        warnings=warnings,
    )
