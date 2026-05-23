"""Provider-capability model for the market screener.

A ``ScreenerCapability`` is one logical signal a data source can supply.
The capability set lets the screener "work well with FMP today and
tolerate a later provider change" (P3 prereq 3 / requirement C4) without
building a second provider now: deferred features (Short Squeeze,
premarket volume) live behind capability flags so a future provider
phase enables them with no rework here.
"""
from __future__ import annotations

from enum import Enum


class ScreenerCapability(str, Enum):
    BASIC_SCREEN = "basic_screen"
    """Market cap / price / volume / sector / industry / exchange /
    country / beta / dividend / isEtf — natively filterable in stage 1
    via FMP ``/company-screener``."""

    DAILY_BARS = "daily_bars"
    """Multi-window momentum (1m / 3m / 6m) and trailing 14-session
    average daily volume — computed from the cached ``DailyBar`` table."""

    INTRADAY_QUOTE = "intraday_quote"
    """Today's volume, gap %, day change %, 50/200-day MA position, P/E,
    EPS, 52-week range — derived from FMP ``/quote`` / ``/batch-quote``
    (premium key)."""

    NEWS_CATALYST = "news_catalyst"
    """``has_positive_catalyst`` boolean — a recent high-materiality
    positive ``NewsItem`` (the P2b news-materiality layer)."""

    SHORT_INTEREST = "short_interest"
    """Short interest / short % of float / days-to-cover. No FMP feed —
    deferred behind this flag until a short-interest provider is added."""

    PREMARKET_VOLUME = "premarket_volume"
    """Clean premarket-session volume. No FMP feed — deferred behind
    this flag until a premarket-data provider is added."""
