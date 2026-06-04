"""P7 §6.4/§6.5 — transaction-cost + capacity model (apps/portfolios).

A small, deterministic helper used by the autopilot bridge:

  * ``dollar_adv`` — 20-session mean(volume × close) from ``get_daily_bars`` (the
    same daily-bar history the screener already fetches), cached per call.
  * ``liquidity_cap_shares`` — the capacity ceiling: an order's notional may not
    exceed ``liquidity_adv_cap_pct`` of the name's dollar-ADV (§6.5, the
    square-root-law discipline "built in early so growth doesn't silently
    degrade fills").
  * ``market_impact_bps`` — a square-root market-impact estimate (impact ∝
    √(notional / dollar-ADV)) for the cost gate (§6.4).

Fallbacks: with <20 sessions use the available window; if volume is missing,
``dollar_adv`` returns None and the caller does NOT block the trade (logged in
``guardrail_actions``).
"""
from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal

ADV_WINDOW = 20
# Coefficient on the √(notional/ADV) impact term, in basis points. A $1 order at
# 1× ADV ⇒ ~50bps; participating at 10% of ADV ⇒ ~16bps. Conservative, tunable.
_IMPACT_COEFF_BPS = 50.0
# Default per-order impact ceiling: skip an order whose estimated impact exceeds
# this (it is too large for the name's liquidity even after the floor).
DEFAULT_MAX_IMPACT_BPS = 75.0


def dollar_adv(ticker: str, as_of, data_provider, *, window: int = ADV_WINDOW) -> Decimal | None:
    """Trailing ``window``-session mean dollar volume (volume × close). Returns
    None when no bars / no volume are available (caller must not block)."""
    start = as_of - dt.timedelta(days=int(window * 1.8) + 10)
    try:
        bars = data_provider.get_daily_bars(ticker, start=start, end=as_of, as_of=as_of) or []
    except Exception:  # noqa: BLE001 — pricing is best-effort
        return None
    bars = bars[-window:]
    vals: list[Decimal] = []
    for b in bars:
        vol = getattr(b, "volume", None)
        close = getattr(b, "close", None)
        if vol is None or close is None:
            continue
        try:
            vals.append(Decimal(str(vol)) * Decimal(str(close)))
        except Exception:  # noqa: BLE001
            continue
    if not vals:
        return None
    return sum(vals, Decimal("0")) / Decimal(len(vals))


def liquidity_cap_shares(adv: Decimal | None, cap_pct, price: Decimal) -> Decimal | None:
    """Max shares so the order notional ≤ ``cap_pct``% of dollar-ADV. None when
    ADV is unknown (no floor applied) or price ≤ 0."""
    if adv is None or price <= 0:
        return None
    cap_notional = adv * (Decimal(str(cap_pct)) / Decimal("100"))
    return (cap_notional / price)


def market_impact_bps(notional: Decimal, adv: Decimal | None) -> float | None:
    """Square-root market-impact estimate in basis points. None when ADV unknown."""
    if adv is None or adv <= 0 or notional <= 0:
        return None
    participation = float(notional) / float(adv)
    return _IMPACT_COEFF_BPS * math.sqrt(participation)
