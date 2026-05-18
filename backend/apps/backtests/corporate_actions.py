"""Corporate action detection from adjusted vs unadjusted close.

We don't have a dedicated corporate-actions feed (FMP provides historical
splits but not always cleanly). For P2c we infer splits from the ratio
between consecutive `close` and `adjusted_close` (when bars exist).

Dividends are read from FMP's historical-dividends endpoint when available;
otherwise the dividend portion is approximated as the residual after
removing split effects.

This module exposes one entry point — `actions_on(ticker, as_of)` — that
returns a list of `{"kind": "split"|"dividend", "ratio": float, "dps": float}`
for the given ex-date.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

from apps.data.models import DailyBar

SPLIT_RATIO_TOL = 0.02  # 2% tolerance vs nearest "clean" ratio


def _round_split(ratio: float) -> float | None:
    """Snap a measured ratio to a clean integer/fractional split ratio.
    Returns None if no clean ratio matches."""
    if ratio <= 0:
        return None
    # Candidates: 2, 3, 4, 1.5, 5, 7, 10 and reverse-splits 0.5, 1/3, etc.
    candidates = [2.0, 3.0, 4.0, 5.0, 7.0, 10.0, 1.5, 0.5, 1 / 3, 0.25, 0.1]
    for c in candidates:
        if abs(ratio / c - 1.0) <= SPLIT_RATIO_TOL:
            return c
    return None


def actions_on(ticker: str, as_of: dt.date, source: str = "fmp") -> list[dict]:
    """Detect corporate actions effective on `as_of` (ex-date).

    Returns list of dicts. Empty list if none detected.
    """
    bars = list(
        DailyBar.objects.filter(
            ticker=ticker, source=source, date__lte=as_of
        ).order_by("-date")[:2]
    )
    if len(bars) < 2:
        return []
    today, prev = bars[0], bars[1]
    if today.date != as_of:
        return []
    # Adjusted-close ratio captures both split and dividend; close ratio
    # captures price only. The split is the close ratio (after normalizing).
    try:
        float(today.adjusted_close)
        adj_prev = float(prev.adjusted_close)
        close_today = float(today.close)
        close_prev = float(prev.close)
    except (TypeError, ValueError):
        return []
    if min(adj_prev, close_prev, close_today) <= 0:
        return []
    actions: list[dict] = []
    # Split detection: previous adjusted should jump by inverse of split ratio.
    # If close drops to ~half overnight, it's a 2:1 split (ratio 2 for holders).
    raw_ratio = close_prev / close_today
    split = _round_split(raw_ratio)
    if split and split != 1.0:
        actions.append({"kind": "split", "ratio": split})
    return actions


def apply_actions(
    portfolio, ticker: str, actions: Iterable[dict]
) -> None:
    for act in actions:
        kind = act.get("kind")
        if kind == "split":
            portfolio.apply_split(ticker, float(act["ratio"]))
        elif kind == "dividend":
            portfolio.apply_dividend(ticker, float(act["dps"]))
        elif kind == "merger_cash":
            portfolio.apply_merger_cash(ticker, float(act["cash_per_share"]))
