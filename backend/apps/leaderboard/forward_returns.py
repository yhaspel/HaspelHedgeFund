"""Forward-return primitive + small stats helpers for the leaderboard (P3b).

``forward_return`` is the missing building block: the realized N-trading-day
return of a ticker as of a decision date, computed from ``DailyBar``. It is the
"ground truth" the agent leaderboard scores persona signals against.
"""
from __future__ import annotations

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
