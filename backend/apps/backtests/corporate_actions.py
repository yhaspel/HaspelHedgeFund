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
    """Return corporate actions effective on `as_of` (ex-date).

    Preferred source: provider-backed `CorporateAction` rows (P2c improvement
    #3). Falls back to the legacy ratio-inferred path from `DailyBar` when no
    provider rows exist for the date, so existing seed data still works.
    """
    # Provider-backed path — explicit rows beat inference.
    from apps.data.models import CorporateAction
    rows = list(
        CorporateAction.objects.filter(ticker=ticker.upper(), as_of_date=as_of)
    )
    if rows:
        out: list[dict] = []
        for r in rows:
            if r.kind == CorporateAction.SPLIT and r.ratio is not None:
                out.append({"kind": "split", "ratio": float(r.ratio)})
            elif r.kind == CorporateAction.CASH_DIVIDEND and r.amount is not None:
                out.append({"kind": "dividend", "dps": float(r.amount)})
            elif r.kind == CorporateAction.MERGER_CASH and r.amount is not None:
                out.append({"kind": "merger_cash", "cash_per_share": float(r.amount)})
            # symbol_change / delisting / stock_dividend — wire when needed.
        return out

    # Legacy fallback: infer the split from the ADJUSTMENT FACTOR jump.
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
    try:
        adj_today = float(today.adjusted_close)
        adj_prev = float(prev.adjusted_close)
        close_today = float(today.close)
        close_prev = float(prev.close)
    except (TypeError, ValueError):
        return []
    if min(adj_today, adj_prev, close_prev, close_today) <= 0:
        return []
    actions: list[dict] = []
    # Engine v2. The old rule read the RAW close ratio (`close_prev /
    # close_today`) and snapped it to a clean split ratio — which cannot tell a
    # 2:1 split from a −50% crash. Every ticker without a CorporateAction row
    # that halved (or tripled, or dropped 90%) on a single session had its qty
    # rescaled and the loss erased from the equity curve.
    #
    # The adjustment factor (adjusted_close / close) is what actually
    # distinguishes them: back-adjusted history leaves adjusted_close CONTINUOUS
    # across a split while close jumps, so the factor jumps by the split ratio.
    # A genuine price move drags close and adjusted_close together, leaving the
    # factor flat. Dividends nudge the factor by well under a percent, far below
    # the smallest split candidate (1.5).
    factor_today = adj_today / close_today
    factor_prev = adj_prev / close_prev
    raw_ratio = factor_today / factor_prev
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
