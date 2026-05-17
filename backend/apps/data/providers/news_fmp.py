"""FMP News adapter (premium tier).

Docs: https://site.financialmodelingprep.com/developer/docs#stock-news
Endpoint: /stable/news/stock?symbols=AAPL — returns headline+text+image+site.
"""
from __future__ import annotations

import datetime as dt

import httpx
from django.conf import settings

from ..models import NewsItem

FMP_BASE = "https://financialmodelingprep.com/stable"


class FmpNewsProvider:
    name = "fmp"

    def __init__(self, api_key: str | None = None, http: httpx.Client | None = None) -> None:
        self.api_key = api_key or settings.FMP_API_KEY
        if not self.api_key:
            raise RuntimeError("FMP_API_KEY is not set")
        self._http = http or httpx.Client(timeout=30.0)

    def fetch(
        self, ticker: str, *, as_of: dt.date, lookback_days: int = 30, limit: int = 100
    ) -> list[NewsItem]:
        start = as_of - dt.timedelta(days=lookback_days)
        params = {
            "symbols": ticker.upper(),
            "from": start.isoformat(),
            "to": as_of.isoformat(),
            "limit": limit,
            "apikey": self.api_key,
        }
        resp = self._http.get(f"{FMP_BASE}/news/stock", params=params)
        resp.raise_for_status()
        out: list[NewsItem] = []
        for item in resp.json():
            pub_raw = item.get("publishedDate") or item.get("published_at")
            if not pub_raw:
                continue
            try:
                pub = dt.datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
            except ValueError:
                try:
                    pub = dt.datetime.strptime(pub_raw, "%Y-%m-%d %H:%M:%S")
                    pub = pub.replace(tzinfo=dt.UTC)
                except ValueError:
                    continue
            if pub.date() > as_of:
                continue
            out.append(
                NewsItem(
                    ticker=ticker.upper(),
                    published_at=pub,
                    headline=(item.get("title") or "")[:512],
                    source=(item.get("site") or item.get("publisher") or "")[:64],
                    provider=self.name,
                    url=(item.get("url") or "")[:1000],
                    summary="",
                    raw_text=(item.get("text") or "")[:8000],
                )
            )
        return out
