"""Disjoint per-period return series for strategy scorecards (wave 3, WP P1).

Why this module exists
----------------------
``PortfolioTarget.marked_snapshot["since_as_of_pct"]`` is a **cumulative**
return measured from that cycle's ``as_of_date`` to the moment the snapshot was
stamped.  Every cycle's window therefore starts at a different date and ends at
(roughly) the same one, so the windows **overlap** — cycle 1 covers day 1..T,
cycle 2 covers day 2..T, and so on.  Chaining them with ``Π(1 + r_i)`` and then
feeding the chain to Sharpe/Sortino/max-DD treats one move in the market as N
independent observations.  That is what produced the production numbers
"Sortino 911.38 / Sharpe 7.24 / total_return 2.08% from 13 cycles".

This module rebuilds the series the honest way: the strategy holds cycle *i*'s
book over the **disjoint** interval ``[as_of_i, as_of_{i+1})`` (the last cycle
runs to ``end_date``), and the period return is that book's return over exactly
that interval — ``Σ_t w_t × (P_t(end) / P_t(start) − 1)``.  Consecutive periods
share only their boundary, so ``Π(1 + r_i)`` telescopes to the strategy's real
cumulative return and the series is a legitimate input to a ratio.

Prices come straight from ``DailyBar`` (the same cache
``apps.portfolios.cycle_mark`` marks against, populated by the FMP provider), in
**one query per strategy** — no provider calls, no API key, no N+1.
"""
from __future__ import annotations

import bisect
import datetime as dt
import statistics
from dataclasses import dataclass

from apps.data.models import DailyBar

# Generous lookback so a boundary landing on a weekend/holiday still finds a
# close (mirrors ``cycle_mark.LOOKBACK_DAYS``).
PRICE_LOOKBACK_DAYS = 14

DAYS_PER_YEAR = 365.25
# Annualisation cadence clamps. A cadence faster than daily trading is not
# meaningful for a daily-close mark, and slower than annual is not a ratio.
MIN_PERIODS_PER_YEAR = 1.0
MAX_PERIODS_PER_YEAR = 252.0


@dataclass(frozen=True)
class Period:
    """One disjoint holding interval and the book's return over it."""

    start: dt.date
    end: dt.date
    ret: float
    days: int


class PriceBook:
    """Batched ``ticker -> (sorted dates, closes)`` lookup over ``DailyBar``.

    Total-return prices (``adjusted_close``) are preferred, falling back to
    ``close`` for rows that predate the adjusted-close backfill. Multiple
    provider rows for the same ``(ticker, date)`` are deduped by keeping the
    freshest fetch — the same rule ``forward_returns.forward_return`` uses.
    """

    def __init__(
        self, tickers, start: dt.date, end: dt.date, *, _rows=None
    ) -> None:
        self._by_ticker: dict[str, tuple[list[dt.date], list[float]]] = {}
        wanted = sorted({str(t).upper() for t in (tickers or []) if t})
        if not wanted or end < start:
            return
        rows = _rows
        if rows is None:
            rows = (
                DailyBar.objects.filter(
                    ticker__in=wanted,
                    date__gte=start - dt.timedelta(days=PRICE_LOOKBACK_DAYS),
                    date__lte=end,
                )
                .order_by("ticker", "date", "-fetched_at")
                .values_list("ticker", "date", "adjusted_close", "close")
                .iterator()
            )
        for ticker, day, adjusted, close in rows:
            dates, prices = self._by_ticker.setdefault(ticker, ([], []))
            if dates and dates[-1] == day:
                continue  # freshest fetch for this date already kept
            px = adjusted if adjusted else close
            if px is None:
                continue
            px = float(px)
            if px <= 0:
                continue
            dates.append(day)
            prices.append(px)

    def close_on_or_before(self, ticker: str, on: dt.date) -> float | None:
        entry = self._by_ticker.get(str(ticker).upper())
        if not entry:
            return None
        dates, prices = entry
        idx = bisect.bisect_right(dates, on)
        if idx == 0:
            return None
        return prices[idx - 1]

    def __bool__(self) -> bool:  # pragma: no cover — convenience
        return bool(self._by_ticker)


def dedupe_targets(targets) -> list:
    """One book per ``as_of_date`` — the last cycle written for a date wins.

    Re-issuing a cycle for the same date does not create a second holding
    period; without this a re-run would show up as a zero-length interval (or,
    worse, as an extra "observation").
    """
    by_date: dict[dt.date, object] = {}
    for t in targets:
        by_date[t.as_of_date] = t
    return [by_date[d] for d in sorted(by_date)]


def book_return(book, prices: PriceBook, start: dt.date, end: dt.date) -> float | None:
    """``Σ w_t × (P_t(end)/P_t(start) − 1)`` for a signed-fraction-of-NAV book.

    Returns ``None`` when the book has legs but none of them can be priced (an
    unmarkable interval must be dropped, not silently recorded as 0%). An
    intentionally empty book is all cash and correctly returns ``0.0``.
    """
    legs = []
    for ticker, weight in (book or {}).items():
        try:
            w = float(weight or 0.0)
        except (TypeError, ValueError):
            continue
        if w:
            legs.append((ticker, w))
    if not legs:
        return 0.0
    total = 0.0
    priced = False
    for ticker, w in legs:
        p0 = prices.close_on_or_before(ticker, start)
        p1 = prices.close_on_or_before(ticker, end)
        if p0 is None or p1 is None or p0 <= 0:
            continue
        total += w * (p1 / p0 - 1.0)
        priced = True
    return total if priced else None


def period_returns(
    targets,
    *,
    end_date: dt.date,
    weights_attr: str = "target_weights",
    prices: PriceBook | None = None,
) -> list[Period]:
    """Disjoint per-period returns for an ordered list of DONE cycles.

    Cycle *i* is held over ``[as_of_i, as_of_{i+1})``; the newest cycle is held
    to ``end_date``. Intervals that cannot be priced are dropped (the caller
    surfaces the count as ``n_observations``), never coerced to zero.
    """
    rows = dedupe_targets(targets)
    if not rows:
        return []
    books = [(t.as_of_date, getattr(t, weights_attr, None) or {}) for t in rows]
    if prices is None:
        tickers = {tk for _, book in books for tk in book}
        prices = PriceBook(tickers, books[0][0], end_date)
    out: list[Period] = []
    for i, (start, book) in enumerate(books):
        end = books[i + 1][0] if i + 1 < len(books) else end_date
        if end <= start:
            continue
        ret = book_return(book, prices, start, end)
        if ret is None:
            continue
        out.append(Period(start=start, end=end, ret=ret, days=(end - start).days))
    return out


def price_book_for(targets, end_date: dt.date, *attrs: str) -> PriceBook:
    """One ``PriceBook`` covering every ticker in every listed weight map."""
    rows = dedupe_targets(targets)
    if not rows:
        return PriceBook([], end_date, end_date)
    tickers: set[str] = set()
    for t in rows:
        for attr in attrs or ("target_weights",):
            tickers.update(getattr(t, attr, None) or {})
    return PriceBook(tickers, rows[0].as_of_date, end_date)


def observed_cadence_days(periods: list[Period]) -> float | None:
    """Median calendar gap between consecutive cycles — the observed cadence."""
    gaps = [p.days for p in periods if p.days > 0]
    if not gaps:
        return None
    median = float(statistics.median(gaps))
    return median if median > 0 else None


def periods_per_year(periods: list[Period]) -> float | None:
    """Annualisation factor from the OBSERVED cadence, not a hard-coded 252.

    A weekly-cadence strategy annualises with ~52, a daily one with ~252 (the
    clamp), a monthly one with ~12. Using 252 for everything is what turned a
    6% mean "cycle return" into a 238,000,000% annualised return.
    """
    cadence = observed_cadence_days(periods)
    if cadence is None:
        return None
    return max(MIN_PERIODS_PER_YEAR, min(MAX_PERIODS_PER_YEAR, DAYS_PER_YEAR / cadence))


def elapsed_days(periods: list[Period]) -> int:
    """Calendar days actually covered by the series (first start -> last end)."""
    if not periods:
        return 0
    return max(0, (periods[-1].end - periods[0].start).days)
