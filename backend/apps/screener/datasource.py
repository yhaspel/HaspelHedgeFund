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
        raise NotImplementedError


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


def get_screener_datasource(user: Any) -> ScreenerDataSource:
    """Build the active screener data source for the given user.

    Raises ``RuntimeError`` with the P2n actionable message if the user
    has no usable FMP key.
    """
    fmp = get_fmp_provider(user=user)
    return FmpScreenerDataSource(fmp)
