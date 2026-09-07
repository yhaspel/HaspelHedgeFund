"""Release-time daily order / notional caps — **shadow mode**.

Owner decision (2026-09): the daily caps are evaluated and RECORDED at release
but never block. The caps were written for the emission path, where they are
applied against a 24h basis that excludes ``pending_open`` orders — so a batch
held over a weekend releases entirely uncapped at the open, and the operator has
no visibility into how far past the limit it went.

This module computes what the caps WOULD have skipped, so the number is on the
run audit (``AutopilotRun.submit_decision["caps_shadow"]``) and on the fund
member card, ready to be promoted to a hard block once the shadow numbers are
trusted. It never mutates an order and never raises into the release path.
"""
from __future__ import annotations

import logging
from decimal import Decimal

log = logging.getLogger(__name__)


def _autopilot_for(order):
    """The autopilot whose caps govern this order: its sleeve's strategy on the
    shared fund account, else the account's linked strategy (legacy)."""
    sleeve = getattr(order, "sleeve", None)
    if sleeve is not None:
        return getattr(sleeve.strategy, "autopilot", None)
    from .autopilot import _strategy_for_account

    strategy = _strategy_for_account(order.broker_account)
    return getattr(strategy, "autopilot", None) if strategy is not None else None


def _order_notional(order, *, user=None) -> Decimal:
    """Best-effort notional for an unfilled order: the fill, the limit, the
    price it was SIZED against on its rebalance row, then the live mark."""
    from . import autopilot_risk

    if order.avg_fill_price:
        price = Decimal(str(order.avg_fill_price))
    else:
        price = autopilot_risk._order_price(order, user=user)
    return (abs(Decimal(str(order.quantity))) * price).quantize(Decimal("0.01"))


def evaluate_release_caps(account, orders) -> dict:
    """Shadow-evaluate the day's caps for a batch about to be released.

    Returns ``{"would_skip": [order_id], "reason": str, "orders": int,
    "notional": Decimal}`` where ``orders``/``notional`` are the DAY's totals
    (the trailing-24h basis plus this batch) and ``would_skip`` names the orders
    a blocking cap would have refused. Caps are per strategy, so on the shared
    fund account each sleeve is counted against its own limits.
    """
    from .autopilot import _prior_24h

    orders = list(orders or [])
    user = getattr(account, "user", None)
    counters: dict[object, dict] = {}
    would_skip: list[int] = []
    reasons: list[str] = []
    day_orders = 0
    day_notional = Decimal("0")
    caps: dict[str, object] = {}

    for order in orders:
        sleeve = getattr(order, "sleeve", None)
        key = sleeve.pk if sleeve is not None else None
        if key not in counters:
            try:
                n_prior, notional_prior = _prior_24h(account, sleeve=sleeve)
            except Exception:  # noqa: BLE001 — shadow accounting must never raise
                log.exception("release cap basis failed account=%s", account.pk)
                n_prior, notional_prior = 0, Decimal("0")
            counters[key] = {
                "n": int(n_prior),
                "notional": Decimal(str(notional_prior)),
                "autopilot": _autopilot_for(order),
            }
            day_orders += int(n_prior)
            day_notional += Decimal(str(notional_prior))
        counter = counters[key]
        notional = _order_notional(order, user=user)
        day_orders += 1
        day_notional += notional

        ap = counter["autopilot"]
        if ap is None:
            continue
        max_orders = ap.max_orders_per_day
        max_notional = (
            Decimal(str(ap.max_notional_per_day_usd))
            if ap.max_notional_per_day_usd is not None else None
        )
        caps.setdefault(
            str(key),
            {
                "max_orders_per_day": max_orders,
                "max_notional_per_day_usd": str(max_notional) if max_notional is not None else None,
            },
        )
        if max_orders is not None and counter["n"] >= max_orders:
            would_skip.append(order.pk)
            reasons.append(f"{order.ticker}: daily order cap ({max_orders})")
            continue
        if max_notional is not None and (counter["notional"] + notional) > max_notional:
            would_skip.append(order.pk)
            reasons.append(f"{order.ticker}: daily notional cap ({max_notional})")
            continue
        counter["n"] += 1
        counter["notional"] += notional

    return {
        "would_skip": would_skip,
        "reason": "; ".join(reasons),
        "orders": day_orders,
        "notional": day_notional.quantize(Decimal("0.01")),
        "caps": caps,
        "shadow": True,
    }
