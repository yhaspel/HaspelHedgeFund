"""Pure metric functions for screener enrichment.

These are intentionally stateless and independently testable. Missing
inputs return a neutral / ``None`` value and the caller adds a per-row
warning — a screen must never 500 because one ticker's bars are thin.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Iterable


def _to_float(value: Decimal | float | int | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def relative_volume(today_volume: int | None, adv_14d: float | None) -> float | None:
    """Today's volume divided by the trailing 14-session ADV.

    ``None`` if either input is missing or the ADV is zero.
    """
    if today_volume is None or adv_14d is None or adv_14d <= 0:
        return None
    return float(today_volume) / float(adv_14d)


def gap_pct(open_price: Decimal | None, previous_close: Decimal | None) -> float | None:
    """Open / previous-close - 1, in **percent** (not ratio)."""
    o = _to_float(open_price)
    p = _to_float(previous_close)
    if o is None or p is None or p == 0:
        return None
    return (o / p - 1.0) * 100.0


def change_pct(price: Decimal | None, previous_close: Decimal | None) -> float | None:
    """Latest price / previous-close - 1, in **percent**."""
    c = _to_float(price)
    p = _to_float(previous_close)
    if c is None or p is None or p == 0:
        return None
    return (c / p - 1.0) * 100.0


def momentum(closes: list[Decimal] | list[float], window_sessions: int) -> float | None:
    """``closes[-1] / closes[-window-1] - 1``, as a ratio (not percent).

    ``None`` if the series is too short or the reference close is zero.
    """
    if not closes or len(closes) <= window_sessions:
        return None
    last = _to_float(closes[-1])
    ref = _to_float(closes[-1 - window_sessions])
    if last is None or ref is None or ref == 0:
        return None
    return (last / ref) - 1.0


def adv(volumes: Iterable[int | float], sessions: int = 14) -> float | None:
    """Mean of the trailing ``sessions`` volumes."""
    vols = [float(v) for v in volumes if v is not None]
    if len(vols) < 1:
        return None
    tail = vols[-sessions:] if len(vols) >= sessions else vols
    if not tail:
        return None
    return sum(tail) / float(len(tail))


def dollar_volume(price: Decimal | None, volume: int | None) -> float | None:
    p = _to_float(price)
    if p is None or volume is None:
        return None
    return p * float(volume)


def distance_from_high(price: Decimal | None, year_high: Decimal | None) -> float | None:
    """Percent below the 52-week high. Positive = below."""
    c = _to_float(price)
    h = _to_float(year_high)
    if c is None or h is None or h == 0:
        return None
    return (1.0 - c / h) * 100.0


def distance_from_low(price: Decimal | None, year_low: Decimal | None) -> float | None:
    """Percent above the 52-week low. Positive = above."""
    c = _to_float(price)
    lo = _to_float(year_low)
    if c is None or lo is None or lo == 0:
        return None
    return (c / lo - 1.0) * 100.0


def is_above(price: Decimal | None, threshold: Decimal | None) -> bool | None:
    p = _to_float(price)
    t = _to_float(threshold)
    if p is None or t is None or t == 0:
        return None
    return p > t
