"""Thin adapter from a concrete data provider to the screener pipeline.

The pipeline, field registry, and preset registry all depend on
``ScreenerDataSource`` rather than on ``FmpProvider`` directly. Adding a
second provider later is therefore an additive change — a new
``ScreenerDataSource`` subclass advertising different capabilities —
without touching ``fields.py``, ``presets.py``, or ``pipeline.py``.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from apps.data.interfaces import Bar, QuoteSnapshot, ScreenerRow
from apps.data.providers.factory import get_fmp_provider
from apps.data.providers.fmp import FmpProvider

from .capabilities import ScreenerCapability

log = logging.getLogger(__name__)

#: ``DailyBar.source`` written by ``FmpProvider`` — the rows the screener's
#: DB-only read may trust.
FMP_BAR_SOURCE = "fmp"


class ScreenerDataSource:
    """Provider adapter advertising a fixed capability set.

    Implementations should be cheap to construct and stateless beyond
    the wrapped provider.
    """

    provider_name: str = "unknown"

    def capabilities(self) -> frozenset:
        return frozenset()

    def screen(self, params: dict[str, Any]) -> list[ScreenerRow]:
        raise NotImplementedError

    def quotes(self, tickers: list[str]) -> dict[str, QuoteSnapshot]:
        raise NotImplementedError

    def daily_bars(
        self, ticker: str, *, end: dt.date, lookback_days: int
    ) -> list[Bar]:
        """Bars for one ticker, fetching from the provider when the local
        cache is thin. One or more HTTP calls — call it sparingly."""
        raise NotImplementedError

    def cached_daily_bars(
        self, tickers: list[str], *, end: dt.date, lookback_days: int
    ) -> dict[str, list[Bar]]:
        """Bars for many tickers, **DB only — never any HTTP**.

        WAVE-3 P2 item 3: a cold-cache screen used to call ``daily_bars`` once
        per candidate, i.e. 300 tickers x 2 bar endpoints = ~600 FMP requests in
        one run, at up to 7,200/min against a 750/min budget shared with the
        live trading pods. Stage 2 now reads this in ONE query and lazily fills
        only a small, bounded slice.
        """
        return {}


class FmpScreenerDataSource(ScreenerDataSource):
    provider_name = "fmp"

    def __init__(self, fmp: FmpProvider) -> None:
        self._fmp = fmp

    def capabilities(self) -> frozenset:
        return frozenset(
            {
                ScreenerCapability.BASIC_SCREEN,
                ScreenerCapability.DAILY_BARS,
                ScreenerCapability.INTRADAY_QUOTE,
                ScreenerCapability.NEWS_CATALYST,
            }
        )

    def screen(self, params: dict[str, Any]) -> list[ScreenerRow]:
        return self._fmp.screen_companies(**params)

    def quotes(self, tickers: list[str]) -> dict[str, QuoteSnapshot]:
        return self._fmp.get_quote_batch(tickers)

    def daily_bars(
        self, ticker: str, *, end: dt.date, lookback_days: int
    ) -> list[Bar]:
        start = end - dt.timedelta(days=lookback_days)
        return self._fmp.get_daily_bars(ticker, start, end, as_of=end)

    def cached_daily_bars(
        self, tickers: list[str], *, end: dt.date, lookback_days: int
    ) -> dict[str, list[Bar]]:
        """One indexed query over ``DailyBar`` for the whole candidate set."""
        from apps.data.models import DailyBar

        if not tickers:
            return {}
        start = end - dt.timedelta(days=lookback_days)
        out: dict[str, list[Bar]] = {}
        rows = DailyBar.objects.filter(
            ticker__in=[t.upper() for t in tickers],
            source=FMP_BAR_SOURCE,
            date__gte=start,
            date__lte=end,
        ).order_by("ticker", "date")
        for r in rows.iterator(chunk_size=2000):
            out.setdefault(r.ticker, []).append(
                Bar(
                    ticker=r.ticker,
                    date=r.date,
                    open=r.open,
                    high=r.high,
                    low=r.low,
                    close=r.close,
                    adjusted_close=r.adjusted_close,
                    volume=r.volume,
                )
            )
        return out


def get_screener_datasource(user: Any) -> ScreenerDataSource:
    """Build the active screener data source for the given user.

    Raises ``RuntimeError`` with the P2n actionable message if the user
    has no usable FMP key.
    """
    fmp = get_fmp_provider(user=user)
    return FmpScreenerDataSource(fmp)
