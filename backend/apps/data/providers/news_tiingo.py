"""Tiingo News adapter (free tier).

Docs: https://www.tiingo.com/documentation/news
Free tier: 1000 requests/hour, headlines + short description.
"""
from __future__ import annotations

import datetime as dt

import httpx
from django.conf import settings

from ..models import NewsItem

TIINGO_BASE = "https://api.tiingo.com/tiingo/news"


class TiingoNewsProvider:
    name = "tiingo"

    def __init__(self, api_key: str | None = None, http: httpx.Client | None = None) -> None:
        self.api_key = api_key or settings.TIINGO_API_KEY
        if not self.api_key:
            raise RuntimeError("TIINGO_API_KEY is not set")
        self._http = http or httpx.Client(timeout=30.0)

    def fetch(
        self, ticker: str, *, as_of: dt.date, lookback_days: int = 30, limit: int = 100
    ) -> list[NewsItem]:
        start = as_of - dt.timedelta(days=lookback_days)
        params = {
            "tickers": ticker.upper(),
            "startDate": start.isoformat(),
            "endDate": as_of.isoformat(),
            "limit": limit,
            "sortBy": "publishedDate",
            "token": self.api_key,
        }
        resp = self._http.get(TIINGO_BASE, params=params)
        resp.raise_for_status()
        out: list[NewsItem] = []
        for item in resp.json():
            pub_raw = item.get("publishedDate") or item.get("crawlDate")
            if not pub_raw:
                continue
            try:
                pub = dt.datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if pub.date() > as_of:
                continue
            out.append(
                NewsItem(
                    ticker=ticker.upper(),
                    published_at=pub,
                    headline=(item.get("title") or "")[:512],
                    source=(item.get("source") or "")[:64],
                    provider=self.name,
                    url=(item.get("url") or "")[:1000],
                    summary=(item.get("description") or "")[:4000],
                    raw_text="",
                )
            )
        return out
