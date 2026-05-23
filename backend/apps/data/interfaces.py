"""DataProvider Protocol. Every provider adapter (FMP, Tiingo, EDGAR, ...)
implements this. The orchestration layer never imports a concrete provider —
it depends only on this protocol so we can swap providers in P2+ without
touching agent code.

Critical invariant: every read is gated on `as_of`. Rows whose
publish/effective date is after `as_of` MUST NOT be returned. This is the
no-cheat guarantee that makes backtests honest.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Bar:
    ticker: str
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adjusted_close: Decimal
    volume: int


@dataclass(frozen=True)
class FundamentalRow:
    ticker: str
    as_of_date: date  # the date this datum became public (point-in-time)
    period_end: date  # the quarter/year it refers to
    metric: str
    value: Decimal


@dataclass(frozen=True)
class ProfileSnapshot:
    """Reference data + latest quote-derived metrics for a ticker (WS-2).

    This is *today* data — `as_of` is the timestamp of the latest quote
    used. Must not be consumed by point-in-time agent paths.
    """
    ticker: str
    name: str
    exchange: str
    sector: str
    price: Decimal | None
    market_cap: Decimal | None
    pe_ratio: Decimal | None
    eps: Decimal | None
    shares_outstanding: int | None
    as_of: date


@dataclass(frozen=True)
class ScreenerRow:
    """One row of FMP `/company-screener` output (P3 prereq 3 stage 1).

    *Today* data — must never be consumed by a point-in-time agent path.
    """
    ticker: str
    name: str
    market_cap: Decimal | None
    price: Decimal | None
    volume: int | None
    beta: Decimal | None
    sector: str
    industry: str
    exchange: str
    country: str
    is_etf: bool
    is_fund: bool
    last_annual_dividend: Decimal | None


@dataclass(frozen=True)
class QuoteSnapshot:
    """Superset of an FMP `/quote` row (P3 prereq 3 stage 2 enrichment).

    Every numeric field is ``Decimal | None`` — if FMP omits a field for a
    given symbol the mapped value is ``None`` and the dependent metric
    degrades to a neutral value with a per-row warning rather than raising.

    *Today* data — must never be consumed by a point-in-time agent path.
    """
    ticker: str
    price: Decimal | None
    open: Decimal | None
    previous_close: Decimal | None
    day_high: Decimal | None
    day_low: Decimal | None
    year_high: Decimal | None
    year_low: Decimal | None
    price_avg_50: Decimal | None
    price_avg_200: Decimal | None
    volume: int | None
    change_pct: Decimal | None
    market_cap: Decimal | None
    pe_ratio: Decimal | None
    eps: Decimal | None
    as_of: date


@dataclass(frozen=True)
class Filing:
    ticker: str
    form_type: str  # "10-K", "10-Q", ...
    filed_at: date
    period_end: date
    accession: str
    url: str
    text_excerpt: str  # short summary or excerpt — not the whole filing


@runtime_checkable
class DataProvider(Protocol):
    """Read-only, point-in-time-correct market data."""

    name: str

    def get_daily_bars(
        self, ticker: str, start: date, end: date, *, as_of: date
    ) -> list[Bar]:
        """Daily OHLCV bars in [start, end]. `as_of` enforces no-look-ahead."""

    def get_fundamentals(
        self, ticker: str, metrics: list[str], *, as_of: date, lookback_quarters: int = 8
    ) -> list[FundamentalRow]:
        """Latest `lookback_quarters` of fundamental metrics with as_of_date <= as_of."""


@runtime_checkable
class FilingsProvider(Protocol):
    name: str

    def get_recent_filings(
        self, ticker: str, *, as_of: date, form_types: list[str], limit: int = 4
    ) -> list[Filing]:
        """Recent filings whose `filed_at <= as_of`."""
