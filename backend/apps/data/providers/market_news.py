"""Market-news aggregator (P3-prereq-4).

Mirrors the existing ticker-scoped ``NewsService`` (``news.py``) but
market-wide and live-only — there is no ``as_of`` here, and the rows are
never consumed by a backtest or PIT path.

Flow: fetch from every available provider → drop in-batch (provider, url)
dupes → assign shared ``dedup_key`` → ``bulk_create(ignore_conflicts=True)``
→ prune rows older than ``RETENTION_DAYS`` → return the recent window.

The freshness short-circuit means one user per 15-min window pays the
provider cost; every other request reuses the persisted rows.
"""
from __future__ import annotations

import datetime as dt
import logging

from django.db import transaction
from django.utils import timezone

from ..models import MarketNewsItem
from ._dedup import dedup_key

log = logging.getLogger(__name__)

FETCH_FRESHNESS_MINUTES = 15
FEED_WINDOW_DAYS = 3
RETENTION_DAYS = 7


class MarketNewsService:
    """Fetches market news from every available provider, dedupes across
    providers, persists, prunes old rows, returns the recent window.

    Provider instances are supplied by the caller (the factory). A ``None``
    provider is skipped — the BYOK gate lives in the factory, not here.
    """

    def __init__(self, fmp=None, tiingo=None) -> None:
        self._providers = [p for p in (fmp, tiingo) if p is not None]
        self.warnings: list[str] = []

    @property
    def providers_used(self) -> list[str]:
        return [getattr(p, "name", "") for p in self._providers if getattr(p, "name", "")]

    def fetch_latest(
        self, *, force: bool = False, per_provider: int = 60
    ) -> list[MarketNewsItem]:
        now = timezone.now()
        self.warnings = []
        # Freshness short-circuit: a fetch in the last 15 minutes by *any*
        # user satisfies subsequent loads. Bypassed only by ``force=True``.
        fresh_exists = MarketNewsItem.objects.filter(
            fetched_at__gte=now - dt.timedelta(minutes=FETCH_FRESHNESS_MINUTES)
        ).exists()

        if force or not fresh_exists:
            collected: list[MarketNewsItem] = []
            for provider in self._providers:
                try:
                    collected.extend(provider.fetch_latest(limit=per_provider))
                except Exception as exc:  # noqa: BLE001 - quota / 401 / 402 / network
                    name = getattr(provider, "name", "?")
                    self.warnings.append(f"{name}: {exc.__class__.__name__}")
                    log.warning(
                        "market_news provider failure provider=%s err=%s", name, exc
                    )
            # Drop in-batch (provider, url) duplicates (general-latest can
            # overlap stock-latest under FMP).
            seen: set[tuple[str, str]] = set()
            to_create: list[MarketNewsItem] = []
            for item in collected:
                pk = (item.provider, item.url)
                if pk in seen:
                    continue
                seen.add(pk)
                item.dedup_key = dedup_key(item.headline, item.published_at)
                to_create.append(item)
            with transaction.atomic():
                if to_create:
                    MarketNewsItem.objects.bulk_create(
                        to_create, ignore_conflicts=True
                    )
                MarketNewsItem.objects.filter(
                    published_at__lt=now - dt.timedelta(days=RETENTION_DAYS)
                ).delete()
        # Re-read so we return persisted rows (with PKs + any prior sentiment).
        return list(
            MarketNewsItem.objects.filter(
                published_at__gte=now - dt.timedelta(days=FEED_WINDOW_DAYS)
            )
        )
