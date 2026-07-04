"""Tiingo Market-News adapter (P3-prereq-4).

Parallel to ``news_tiingo.py`` but market-wide:

- ``GET /tiingo/news`` with **no** ``tickers`` parameter returns the latest
  crawled stories across all sources, newest-first. Each row carries
  ``title``, ``description``, ``url``, ``publishedDate``, ``crawlDate``,
  ``source``, ``tags`` (topic tags), and ``tickers`` (symbols the Tiingo
  algorithm associated with the story).

Mapped onto ``MarketNewsItem`` for aggregation by ``MarketNewsService``.
"""
from __future__ import annotations

import datetime as dt

import httpx
from django.conf import settings

from ..models import MarketNewsItem
from ._http import make_client

TIINGO_BASE = "https://api.tiingo.com/tiingo/news"
RETENTION_DAYS = 7


def _parse_pub(raw: str) -> dt.datetime | None:
    if not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed


class MarketNewsTiingoProvider:
    name = "tiingo"

    def __init__(
        self, api_key: str | None = None, http: httpx.Client | None = None
    ) -> None:
        self.api_key = api_key or settings.TIINGO_API_KEY
        if not self.api_key:
            raise RuntimeError("TIINGO_API_KEY is not set")
        self._http = http or make_client(timeout=30.0)

    def fetch_latest(self, *, limit: int = 60) -> list[MarketNewsItem]:
        params = {
            "sortBy": "publishedDate",
            "limit": limit,
            "token": self.api_key,
        }
        resp = self._http.get(TIINGO_BASE, params=params)
        resp.raise_for_status()
        rows = resp.json()
        if not isinstance(rows, list):
            return []

        retention_cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=RETENTION_DAYS)
        out: list[MarketNewsItem] = []
        for item in rows:
            headline = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            if not headline or not url:
                continue
            pub = _parse_pub(item.get("publishedDate") or "") or _parse_pub(
                item.get("crawlDate") or ""
            )
            if pub is None or pub < retention_cutoff:
                continue
            tickers = item.get("tickers") or []
            symbols = [str(t).upper() for t in tickers if t]
            tags = item.get("tags") or []
            tags = [str(t) for t in tags if t]
            out.append(
                MarketNewsItem(
                    provider=self.name,
                    headline=headline[:512],
                    summary=(item.get("description") or "")[:4000],
                    url=url[:1000],
                    image_url="",
                    source=(item.get("source") or "")[:128],
                    published_at=pub,
                    symbols=symbols,
                    tags=tags,
                )
            )
        return out
