"""P02b review: news agent must emit freshness metadata so the UI can
distinguish degraded provider state from a genuinely neutral signal.
"""
from __future__ import annotations

import datetime as dt

import pytest

from apps.data.models import NewsItem
from hedgefund_agents.news.news_agent import _news_freshness

pytestmark = pytest.mark.django_db


def _mk(provider: str, source: str, *, pub: dt.datetime, url: str) -> NewsItem:
    return NewsItem.objects.create(
        ticker="AAPL",
        published_at=pub,
        headline=f"H {pub.isoformat()}",
        source=source,
        provider=provider,
        url=url,
        summary="",
        dedup_key=f"k{url}",
    )


def test_freshness_fresh_items_not_stale():
    as_of = dt.date(2024, 12, 31)
    items = [
        _mk(
            "tiingo", "Reuters",
            pub=dt.datetime(2024, 12, 30, 12, tzinfo=dt.UTC),
            url="https://a/1",
        ),
        _mk(
            "fmp", "Bloomberg",
            pub=dt.datetime(2024, 12, 28, 12, tzinfo=dt.UTC),
            url="https://b/2",
        ),
    ]
    f = _news_freshness(items, as_of)
    assert f["stale"] is False
    assert f["partial"] is False
    assert f["fallback"] is False
    assert set(f["providers"]) == {"tiingo", "fmp"}
    assert f["item_count"] == 2
    assert f["newest_age_days"] in (0, 1)


def test_freshness_marks_old_items_as_stale():
    """Newest item more than 7 days old → stale."""
    as_of = dt.date(2024, 12, 31)
    items = [
        _mk(
            "tiingo", "Reuters",
            pub=dt.datetime(2024, 12, 15, tzinfo=dt.UTC),
            url="https://a/old",
        ),
    ]
    f = _news_freshness(items, as_of)
    assert f["stale"] is True
    assert f["newest_age_days"] >= 7
    assert f["partial"] is False


def test_freshness_zero_items_is_partial_not_stale():
    """No items → partial=True. The UI should warn 'provider returned empty'
    rather than treating an absent digest as a neutral signal."""
    f = _news_freshness([], dt.date(2024, 12, 31))
    assert f["item_count"] == 0
    assert f["partial"] is True
    assert f["stale"] is False
