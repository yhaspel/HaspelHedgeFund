"""P10 §E3 — the news-sentiment LAB scheduler (strategy #56-class sleeves).

The forward experiment was structurally unable to produce an answer: 1 manual
cycle ever (no schedule), and its signal supply — news fetch + LLM sentiment
classification — ran only from the news-page VIEW, so an unattended week
silently starved the signal (422/3,169 items scored in 30d at audit).

Two beat tasks repair that:

* ``fetch_lab_news`` (daily) — SYMBOL-TARGETED news fetch for every active
  ``news_sentiment`` strategy's universe (FMP /news/stock), persisted through
  the same dedup pipeline, then classified with the **frozen** experiment
  model (``settings.NEWS_LAB_SENTIMENT_MODEL`` — a mid-experiment model swap
  would change the sleeve's signal definition).
* ``run_news_lab_cycles`` (weekly, Friday after the fund pods) — refreshes the
  news, then fires ``daily_long_short_cycle`` for each lab sleeve.

Lab sleeves are NOT autopilots on purpose: they have no broker link and the
§9 gate (correctly) can't pass for an unbacktestable kind, so the standard
enable path is closed. This is the deliberate side door for paper-only lab
strategies; their books stay hypothetical.
"""
from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import PortfolioStrategy

log = logging.getLogger(__name__)

CLASSIFY_BATCH = 48  # mirrors the news page's per-request classify ceiling


def _lab_strategies():
    return PortfolioStrategy.objects.filter(
        kind=PortfolioStrategy.KIND_NEWS_SENTIMENT, is_active=True,
    )


def _lab_universe(strategy) -> list[str]:
    from .tasks import _active_members

    return sorted({t for t, _sector in _active_members(strategy, timezone.localdate())})


@shared_task(name="apps.portfolios.tasks_lab.fetch_lab_news")
def fetch_lab_news() -> dict:
    """Symbol-targeted news fetch + frozen-model classification for the lab
    universes — decoupled from the news-page view."""
    import datetime as dt

    from apps.data.market_news_sentiment import classify, is_allowed_sentiment_model
    from apps.data.models import MarketNewsItem
    from apps.data.providers._dedup import dedup_key
    from apps.data.providers.factory import get_market_news_fmp_provider

    fetched = scored = 0
    for strategy in _lab_strategies():
        tickers = _lab_universe(strategy)
        if not tickers:
            continue
        try:
            provider = get_market_news_fmp_provider(strategy.user)
        except RuntimeError as exc:
            log.warning("news lab fetch: no FMP key for strategy=%s: %s", strategy.pk, exc)
            continue
        try:
            items = provider.fetch_for_symbols(tickers)
        except Exception:  # noqa: BLE001 — provider failure must not kill the beat
            log.exception("news lab fetch failed strategy=%s", strategy.pk)
            continue
        seen: set[tuple[str, str]] = set()
        to_create = []
        for item in items:
            pk = (item.provider, item.url)
            if pk in seen:
                continue
            seen.add(pk)
            item.dedup_key = dedup_key(item.headline, item.published_at)
            to_create.append(item)
        with transaction.atomic():
            if to_create:
                MarketNewsItem.objects.bulk_create(to_create, ignore_conflicts=True)
        fetched += len(to_create)

        # Classify unscored lab-universe rows with the FROZEN model.
        model_id = getattr(
            settings, "NEWS_LAB_SENTIMENT_MODEL", "openrouter:qwen/qwen3.6-27b",
        )
        if not is_allowed_sentiment_model(model_id):
            log.error("news lab: frozen model %r not in the allow-list", model_id)
            continue
        window_start = timezone.now() - dt.timedelta(days=7)
        tset = set(tickers)
        # symbols is a JSONField (list) — intersect in Python; the 7-day window
        # is bounded by the feed's own retention.
        rows = [
            r for r in MarketNewsItem.objects.filter(
                published_at__gte=window_start, sentiment_score__isnull=True,
            ).order_by("-published_at")
            if tset.intersection(r.symbols or [])
        ]
        for i in range(0, len(rows), CLASSIFY_BATCH):
            ok, warn = classify(
                rows[i:i + CLASSIFY_BATCH], model_id=model_id,
                user_id=strategy.user_id,
            )
            if not ok:
                log.warning("news lab classify warning strategy=%s: %s", strategy.pk, warn)
                break
            scored += len(rows[i:i + CLASSIFY_BATCH])
    return {"fetched": fetched, "scored": scored}


@shared_task(name="apps.portfolios.tasks_lab.run_news_lab_cycles")
def run_news_lab_cycles() -> dict:
    """Weekly lab cycle: refresh the signal, then run each lab sleeve's cycle
    (Friday, after the fund pods' 20:30/20:45/21:00 UTC fires)."""
    from .tasks import daily_long_short_cycle

    try:
        fetch_lab_news()
    except Exception:  # noqa: BLE001 — run the cycle on stale news rather than skip
        log.exception("news lab pre-cycle fetch failed; cycling on existing rows")

    dispatched = []
    for strategy in _lab_strategies():
        try:
            result = daily_long_short_cycle(strategy.pk, force=True)
            dispatched.append({"strategy_id": strategy.pk, "result": result})
        except Exception:  # noqa: BLE001 — one sleeve must not block another
            log.exception("news lab cycle failed strategy=%s", strategy.pk)
            dispatched.append({"strategy_id": strategy.pk, "result": "error"})
    return {"cycles": dispatched}
