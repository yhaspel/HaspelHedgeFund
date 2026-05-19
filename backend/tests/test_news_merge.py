"""News cache must merge new provider rows even when one cached row exists.

Reproduces the bug behind P2b improvement #5: a single stale row used to
short-circuit `fetch_and_persist`, blocking newer items from being pulled in.
"""
from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.data.models import NewsItem
from apps.data.providers.news import NewsService


class FakeProvider:
    def __init__(self, name: str, items: list[NewsItem]) -> None:
        self.name = name
        self._items = items

    def fetch(self, ticker: str, *, as_of: dt.date, lookback_days: int) -> list[NewsItem]:
        return list(self._items)


def _row(ticker: str, *, days_ago: int, provider: str, url: str, headline: str) -> NewsItem:
    return NewsItem(
        ticker=ticker.upper(),
        published_at=timezone.now() - dt.timedelta(days=days_ago),
        headline=headline,
        source=provider,
        provider=provider,
        url=url,
        summary="",
        raw_text="",
    )


@pytest.mark.django_db
def test_stale_cache_does_not_block_new_provider_rows() -> None:
    as_of = dt.date.today()
    # One stale cached row from `tiingo`, ~10 days ago.
    NewsItem.objects.create(
        ticker="AAPL",
        published_at=timezone.now() - dt.timedelta(days=10),
        headline="Old headline about Apple",
        source="tiingo",
        provider="tiingo",
        url="https://tiingo.example/old",
        summary="",
        raw_text="",
        dedup_key="x" * 32,
    )
    fresh_fmp = _row("AAPL", days_ago=0, provider="fmp",
                     url="https://fmp.example/fresh",
                     headline="Apple ships new product today")
    svc = NewsService.__new__(NewsService)
    svc._providers = [FakeProvider("tiingo", []), FakeProvider("fmp", [fresh_fmp])]

    out = svc.fetch_and_persist("AAPL", as_of=as_of, lookback_days=30)

    headlines = {r.headline for r in out}
    assert "Apple ships new product today" in headlines, (
        "fresh FMP row should have been fetched and merged despite the stale tiingo cache"
    )
    assert NewsItem.objects.filter(provider="fmp").count() == 1


@pytest.mark.django_db
def test_fresh_full_cache_short_circuits() -> None:
    """When every provider has a row within the freshness window, skip refetch."""
    as_of = dt.date.today()
    for p in ("tiingo", "fmp"):
        NewsItem.objects.create(
            ticker="AAPL",
            published_at=timezone.now(),
            headline=f"{p} headline",
            source=p,
            provider=p,
            url=f"https://{p}.example/today",
            summary="",
            raw_text="",
            dedup_key=p * 16,
        )

    called: list[str] = []

    class Tracking(FakeProvider):
        def fetch(self, ticker, *, as_of, lookback_days):
            called.append(self.name)
            return []

    svc = NewsService.__new__(NewsService)
    svc._providers = [Tracking("tiingo", []), Tracking("fmp", [])]

    svc.fetch_and_persist("AAPL", as_of=as_of, lookback_days=30)
    assert called == [], f"expected no provider fetches; got {called}"
