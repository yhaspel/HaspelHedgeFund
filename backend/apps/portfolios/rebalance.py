"""Rebalancer — current portfolio + target → ordered list of RebalanceOrders.

Three waves:
  sequence=0  closes (positions not in target)
  sequence=1  resizes (existing positions whose target weight changed)
  sequence=2  opens (target positions with no existing position)

Filters: min_trade_notional_usd, max_turnover_pct (proportional scale).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CurrentPosition:
    ticker: str
    quantity: float  # negative = short
    avg_cost: float
    sector: str = ""


@dataclass
class RebalanceConfig:
    portfolio_value: float
    last_close: dict[str, float]               # {ticker: last_close}
    min_trade_notional_usd: float = 250.0
    max_turnover_pct: float = 0.30
    limit_price_buffer_pct: float = 0.005      # ±0.5% buffer around last_close


@dataclass
class Order:
    ticker: str
    side: str  # "buy" | "sell" | "short" | "cover"
    quantity: float
    limit_price: float | None
    reason: str
    estimated_notional_usd: float
    sequence: int


def _limit_price(side: str, last: float, buf: float) -> float | None:
    if last <= 0:
        return None
    if side in ("buy", "cover"):
        return round(last * (1.0 + buf), 4)
    return round(last * (1.0 - buf), 4)


def _qty_for_weight(weight: float, portfolio_value: float, price: float) -> float:
    if price <= 0:
        return 0.0
    return (weight * portfolio_value) / price


def compute_orders(
    current: list[CurrentPosition],
    target_weights: dict[str, float],   # signed
    cfg: RebalanceConfig,
) -> list[Order]:
    orders: list[Order] = []
    cur_by_t = {p.ticker: p for p in current}

    # Wave 0: closes (in current, not in target)
    for t, pos in cur_by_t.items():
        if t in target_weights and abs(target_weights[t]) > 0:
            continue
        if abs(pos.quantity) < 1e-9:
            continue
        last = cfg.last_close.get(t, float(pos.avg_cost or 0.0))
        side = "sell" if pos.quantity > 0 else "cover"
        qty = abs(pos.quantity)
        notional = qty * last
        orders.append(Order(
            ticker=t, side=side, quantity=qty,
            limit_price=_limit_price(side, last, cfg.limit_price_buffer_pct),
            reason="close", estimated_notional_usd=notional, sequence=0,
        ))

    # Waves 1 & 2: resize / open
    for t, w in target_weights.items():
        price = cfg.last_close.get(t, 0.0)
        target_qty = _qty_for_weight(w, cfg.portfolio_value, price)
        existing = cur_by_t.get(t)
        existing_qty = float(existing.quantity) if existing else 0.0
        delta = target_qty - existing_qty
        if abs(delta * price) < cfg.min_trade_notional_usd:
            continue
        if existing is None:
            # Open
            qty = abs(delta)
            side = "buy" if delta > 0 else "short"
            orders.append(Order(
                ticker=t, side=side, quantity=qty,
                limit_price=_limit_price(side, price, cfg.limit_price_buffer_pct),
                reason="open", estimated_notional_usd=qty * price, sequence=2,
            ))
        else:
            qty = abs(delta)
            if existing_qty >= 0 and delta > 0:
                side, reason = "buy", "resize_up"
            elif existing_qty >= 0 and delta < 0:
                side, reason = "sell", "resize_down"
            elif existing_qty < 0 and delta < 0:
                side, reason = "short", "resize_up"
            else:
                side, reason = "cover", "resize_down"
            orders.append(Order(
                ticker=t, side=side, quantity=qty,
                limit_price=_limit_price(side, price, cfg.limit_price_buffer_pct),
                reason=reason, estimated_notional_usd=qty * price, sequence=1,
            ))

    # Turnover cap — risk-reducing orders are exempt; the cap restricts
    # additions of exposure. Closes / resize_down are stop-out / de-risk
    # actions and must always run in full, even if they alone exceed the
    # cap. Only resize_up + opens are scaled down to fit whatever notional
    # remains under the cap.
    turnover_cap_usd = cfg.max_turnover_pct * cfg.portfolio_value
    if turnover_cap_usd > 0:
        risk_off_reasons = {"close", "resize_down"}
        risk_off = [o for o in orders if o.reason in risk_off_reasons]
        risk_on = [o for o in orders if o.reason not in risk_off_reasons]
        risk_on_notional = sum(o.estimated_notional_usd for o in risk_on)
        if risk_on_notional > turnover_cap_usd > 0:
            scale = turnover_cap_usd / risk_on_notional
            for o in risk_on:
                o.quantity *= scale
                o.estimated_notional_usd *= scale
        orders = risk_off + risk_on

    orders.sort(key=lambda o: (o.sequence, o.ticker))
    return orders
