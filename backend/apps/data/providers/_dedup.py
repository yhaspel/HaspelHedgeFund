"""Shared dedup helper for news.

Both the ticker-scoped ``NewsService`` (``news.py``) and the market-news
``MarketNewsService`` (``market_news.py``) use this to bucket near-duplicate
headlines from different providers. Two items with the same normalised
headline within a 24h calendar-day window collide.

Behaviour matches the previous private ``_dedup_key`` in ``news.py`` exactly —
this is a refactor, not a behaviour change. The ``NewsService`` test suite is
the regression guard.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def dedup_key(headline: str, published_at: dt.datetime) -> str:
    """Return a stable 32-char hex key for near-duplicate headline grouping.

    Lower-cases, strips non-alphanumerics, takes the first 12 words (captures
    the gist while ignoring trailing source attribution), and buckets by
    calendar day. SHA-256 → 32 hex chars.
    """
    norm = _NORMALIZE_RE.sub(" ", headline.lower()).strip()
    norm = " ".join(norm.split()[:12])
    day_bucket = published_at.strftime("%Y-%m-%d")
    return hashlib.sha256(f"{norm}|{day_bucket}".encode()).hexdigest()[:32]
