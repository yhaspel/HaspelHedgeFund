"""Forward-return primitive + small stats helpers for the leaderboard (P3b).

``forward_return`` is the missing building block: the realized N-trading-day
return of a ticker as of a decision date, computed from ``DailyBar``. It is the
"ground truth" the agent leaderboard scores persona signals against.
"""
from __future__ import annotations

import bisect
import datetime as dt
import math

from apps.data.models import DailyBar

DEFAULT_FORWARD_DAYS = 5


def forward_return(ticker: str, as_of: dt.date, n_days: int = DEFAULT_FORWARD_DAYS) -> float | None:
    """Realized return from the first close on/after ``as_of`` to the close
    ``n_days`` trading days later. Returns None when there aren't enough bars
    yet (a recent decision whose forward window hasn't elapsed).

    Dedupes multiple provider rows per date (DailyBar is unique per
    ticker/date/source) by keeping the first seen per date.
    """
    rows = (
        DailyBar.objects.filter(ticker=ticker.upper(), date__gte=as_of)
        .order_by("date", "-fetched_at")
        .values_list("date", "adjusted_close")
    )
    series: list[float] = []
    seen: set[dt.date] = set()
    for d, close in rows.iterator():
        if d in seen:
            continue
        seen.add(d)
        series.append(float(close))
        if len(series) > n_days:
            break
    if len(series) < n_days + 1:
        return None
    entry, exit_ = series[0], series[n_days]
    if entry <= 0:
        return None
    return (exit_ - entry) / entry


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion — honest CI on small samples."""
    if n == 0:
        return (0.0, 0.0)
    phat = successes / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    margin = (z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def brier(prob: float, outcome: int) -> float:
    """Squared error between a [0,1] probability and a {0,1} outcome."""
    return (prob - outcome) ** 2


def batch_forward_returns(
    pairs: list[tuple[str, dt.date]], n_days: int = DEFAULT_FORWARD_DAYS
) -> dict[tuple[str, dt.date], float | None]:
    """``forward_return`` for many ``(ticker, as_of)`` pairs in ONE query.

    Same semantics as :func:`forward_return` (first close on/after ``as_of`` ->
    the close ``n_days`` trading days later, ``None`` when the window has not
    elapsed), but the scorecard recompute and the drill-down no longer issue one
    ``DailyBar`` query per decision (the review's F-n+1 finding: the agent
    drill-down grew a query per row, unbounded).
    """
    wanted = {(t.upper(), d) for t, d in pairs}
    if not wanted:
        return {}
    tickers = sorted({t for t, _ in wanted})
    earliest = min(d for _, d in wanted)
    rows = (
        DailyBar.objects.filter(ticker__in=tickers, date__gte=earliest)
        .order_by("ticker", "date", "-fetched_at")
        .values_list("ticker", "date", "adjusted_close")
        .iterator()
    )
    series: dict[str, tuple[list[dt.date], list[float]]] = {}
    for ticker, day, close in rows:
        dates, prices = series.setdefault(ticker, ([], []))
        if dates and dates[-1] == day:
            continue  # freshest fetch for this date already kept
        if close is None:
            continue
        dates.append(day)
        prices.append(float(close))

    out: dict[tuple[str, dt.date], float | None] = {}
    for ticker, as_of in wanted:
        entry = series.get(ticker)
        if not entry:
            out[(ticker, as_of)] = None
            continue
        dates, prices = entry
        start = bisect.bisect_left(dates, as_of)
        if start + n_days >= len(prices):
            out[(ticker, as_of)] = None
            continue
        entry_px, exit_px = prices[start], prices[start + n_days]
        out[(ticker, as_of)] = (exit_px - entry_px) / entry_px if entry_px > 0 else None
    return out
