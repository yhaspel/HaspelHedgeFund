"""News aggregation dedupes near-duplicate headlines across providers."""
from __future__ import annotations

import datetime as dt

import pytest

from apps.data.models import NewsItem
from apps.data.providers.news import NewsService, _dedup_key

pytestmark = pytest.mark.django_db


def _make(provider: str, headline: str, *, pub: dt.datetime, url: str) -> NewsItem:
    return NewsItem.objects.create(
        ticker="AAPL",
        published_at=pub,
        headline=headline,
        source=f"src-{provider}",
        provider=provider,
        url=url,
        summary="",
        dedup_key=_dedup_key(headline, pub),
    )


def test_paraphrased_headlines_same_day_collapse_to_one():
    pub = dt.datetime(2024, 12, 30, 10, 0, tzinfo=dt.UTC)
    _make("tiingo", "Apple reports record iPhone sales for Q4 2024", pub=pub, url="https://a.example/1")
    _make("fmp",    "Apple reports record iPhone sales for q4 2024!", pub=pub, url="https://b.example/1")
    rows = NewsService._dedup(list(NewsItem.objects.all()))
    assert len(rows) == 1


def test_distinct_headlines_are_kept():
    pub = dt.datetime(2024, 12, 30, 10, 0, tzinfo=dt.UTC)
    _make("tiingo", "Apple reports record iPhone sales for Q4", pub=pub, url="https://a.example/1")
    _make("fmp",    "Apple settles patent suit with Masimo",     pub=pub, url="https://b.example/2")
    rows = NewsService._dedup(list(NewsItem.objects.all()))
    assert len(rows) == 2


def test_same_headline_different_days_kept_separate():
    h = "Apple announces buyback expansion"
    _make("tiingo", h, pub=dt.datetime(2024, 12, 1, tzinfo=dt.UTC), url="https://a.example/x")
    _make("tiingo", h, pub=dt.datetime(2024, 12, 28, tzinfo=dt.UTC), url="https://a.example/y")
    rows = NewsService._dedup(list(NewsItem.objects.all()))
    assert len(rows) == 2
