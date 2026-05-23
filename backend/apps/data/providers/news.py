"""News aggregator: fetches from Tiingo + FMP, dedupes, persists, returns rows."""
from __future__ import annotations

import datetime as dt

from django.db import transaction

from ..models import NewsItem
from ._dedup import dedup_key as _dedup_key
from .news_fmp import FmpNewsProvider
from .news_tiingo import TiingoNewsProvider


class NewsService:
    """Combines Tiingo + FMP, persists, returns deduped NewsItems for a ticker.

    P2n: provider instances must be supplied by the caller (typically via
    ``apps.data.providers.factory.get_news_service``). When ``None`` is passed,
    that provider is silently skipped — the BYOK gate is enforced in the
    factory, not here.
    """

    def __init__(
        self,
        tiingo: TiingoNewsProvider | None = None,
        fmp: FmpNewsProvider | None = None,
    ) -> None:
        self._providers = [p for p in (tiingo, fmp) if p is not None]

    def fetch_and_persist(
        self, ticker: str, *, as_of: dt.date, lookback_days: int = 30
    ) -> list[NewsItem]:
        existing = list(
            NewsItem.objects.filter(
                ticker=ticker.upper(),
                published_at__date__gte=as_of - dt.timedelta(days=lookback_days),
                published_at__date__lte=as_of,
            )
        )
        # Cache freshness rule: only short-circuit if we have at least one
        # provider's row *and* a row within the last day of as_of. A single
        # stale row from one provider must not block fetching newer rows or
        # additional providers' rows. (Dedup_key + ignore_conflicts make the
        # merge safe.)
        freshness_cutoff = as_of - dt.timedelta(days=1)
        has_fresh = any(r.published_at.date() >= freshness_cutoff for r in existing)
        sources_present = {r.source for r in existing}
        expected_sources = {getattr(p, "name", "") for p in self._providers}
        if has_fresh and expected_sources.issubset(sources_present | {""}):
            return self._dedup(existing)

        collected: list[NewsItem] = []
        for provider in self._providers:
            try:
                collected.extend(
                    provider.fetch(ticker, as_of=as_of, lookback_days=lookback_days)
                )
            except Exception:
                continue

        for item in collected:
            item.dedup_key = _dedup_key(item.headline, item.published_at)

        with transaction.atomic():
            NewsItem.objects.bulk_create(collected, ignore_conflicts=True)

        rows = list(
            NewsItem.objects.filter(
                ticker=ticker.upper(),
                published_at__date__gte=as_of - dt.timedelta(days=lookback_days),
                published_at__date__lte=as_of,
            )
        )
        return self._dedup(rows)

    @staticmethod
    def _dedup(items: list[NewsItem]) -> list[NewsItem]:
        seen: dict[str, NewsItem] = {}
        for it in sorted(items, key=lambda x: x.published_at, reverse=True):
            key = it.dedup_key or _dedup_key(it.headline, it.published_at)
            if key not in seen:
                seen[key] = it
        return sorted(seen.values(), key=lambda x: x.published_at, reverse=True)
