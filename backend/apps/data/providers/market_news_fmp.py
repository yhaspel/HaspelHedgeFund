"""FMP Market-News adapter (P3-prereq-4).

Parallel to ``news_fmp.py`` but market-wide, not ticker-scoped:

- ``GET /stable/news/general-latest`` — broad financial market news, refreshed
  daily per FMP's docs.
- ``GET /stable/news/stock-latest`` — company-tagged stock news (each row
  carries a ``symbol``), refreshed in real time.

Both rows map onto ``MarketNewsItem``. The aggregator
(``MarketNewsService``) merges this provider with Tiingo and dedupes across
providers.
"""
from __future__ import annotations

import datetime as dt

import httpx
from django.conf import settings

from ..models import MarketNewsItem

FMP_BASE = "https://financialmodelingprep.com/stable"
RETENTION_DAYS = 7


def _parse_pub(raw: str) -> dt.datetime | None:
    """Robust ``publishedDate`` parsing — ISO-8601 with the "%Y-%m-%d %H:%M:%S"
    fallback, mirroring ``news_fmp.py``. Always returns an offset-aware UTC
    datetime so cross-comparisons with ``timezone.now()`` are safe.
    """
    if not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = dt.datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed


class MarketNewsFmpProvider:
    name = "fmp"

    def __init__(
        self, api_key: str | None = None, http: httpx.Client | None = None
    ) -> None:
        self.api_key = api_key or settings.FMP_API_KEY
        if not self.api_key:
            raise RuntimeError("FMP_API_KEY is not set")
        self._http = http or httpx.Client(timeout=30.0)

    def _fetch_endpoint(self, path: str, limit: int, **extra) -> list[dict]:
        params = {"page": 0, "limit": limit, "apikey": self.api_key, **extra}
        resp = self._http.get(f"{FMP_BASE}{path}", params=params)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    def fetch_for_symbols(
        self, symbols: list[str], *, limit: int = 100,
    ) -> list[MarketNewsItem]:
        """P10 §E3: SYMBOL-TARGETED stock news (FMP /news/stock?symbols=…) for
        the news-lab universe — the general stock-latest feed only skims the
        most recent market-wide items, so a 200-name universe starves unless
        someone browses the news page. Batched ≤50 symbols per request."""
        retention_cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=RETENTION_DAYS)
        out: list[MarketNewsItem] = []
        cleaned = sorted({str(s).upper() for s in symbols if s})
        for i in range(0, len(cleaned), 50):
            batch = cleaned[i:i + 50]
            rows = self._fetch_endpoint(
                "/news/stock", limit, symbols=",".join(batch),
            )
            for item in rows:
                headline = (item.get("title") or "").strip()
                url = (item.get("url") or "").strip()
                if not headline or not url:
                    continue
                pub = _parse_pub(item.get("publishedDate") or item.get("published_at") or "")
                if pub is None or pub < retention_cutoff:
                    continue
                sym = item.get("symbol")
                out.append(
                    MarketNewsItem(
                        provider=self.name,
                        headline=headline[:512],
                        summary=(item.get("text") or "")[:4000],
                        url=url[:1000],
                        image_url=(item.get("image") or "")[:1000],
                        source=(item.get("site") or item.get("publisher") or "")[:128],
                        published_at=pub,
                        symbols=[str(sym).upper()] if sym else [],
                        tags=[],
                    )
                )
        return out

    def fetch_latest(self, *, limit: int = 60) -> list[MarketNewsItem]:
        """Pull both general-latest and stock-latest, map to ``MarketNewsItem``."""
        retention_cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=RETENTION_DAYS)
        out: list[MarketNewsItem] = []

        for path, is_stock in (
            ("/news/general-latest", False),
            ("/news/stock-latest", True),
        ):
            try:
                rows = self._fetch_endpoint(path, limit)
            except Exception:
                # One sub-feed failing must not block the other; surface via
                # the aggregator's warnings on raise.
                raise
            for item in rows:
                headline = (item.get("title") or "").strip()
                url = (item.get("url") or "").strip()
                if not headline or not url:
                    continue
                pub = _parse_pub(item.get("publishedDate") or item.get("published_at") or "")
                if pub is None or pub < retention_cutoff:
                    continue
                symbols: list[str] = []
                if is_stock:
                    sym = item.get("symbol")
                    if sym:
                        symbols = [str(sym).upper()]
                out.append(
                    MarketNewsItem(
                        provider=self.name,
                        headline=headline[:512],
                        summary=(item.get("text") or "")[:4000],
                        url=url[:1000],
                        image_url=(item.get("image") or "")[:1000],
                        source=(item.get("site") or item.get("publisher") or "")[:128],
                        published_at=pub,
                        symbols=symbols,
                        tags=[],
                    )
                )
        return out
