"""Per-user screener services (saved screens + watchlist)."""
from __future__ import annotations

from typing import Any, Iterable

from .models import Watchlist, WatchlistItem

# Plan §10/§12: 100-item cap kept in lockstep with the view.
WATCHLIST_CAP = 100


def get_or_create_watchlist(user: Any) -> Watchlist:
    wl, _ = Watchlist.objects.get_or_create(user=user)
    return wl


def add_tickers_to_watchlist(user: Any, tickers: Iterable[str]) -> int:
    """Idempotently merge ``tickers`` into ``user``'s watchlist.

    Upper-cases, de-dupes against existing items, respects the 100-item cap.
    Returns the number actually added. Safe to call with an empty / falsy list.
    """
    if not tickers:
        return 0
    wl = get_or_create_watchlist(user)
    existing = set(
        t.upper() for t in wl.items.values_list("ticker", flat=True)
    )
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in tickers:
        if not isinstance(raw, str):
            continue
        t = raw.strip().upper()
        if not t or t in seen or t in existing:
            continue
        seen.add(t)
        cleaned.append(t)
    if not cleaned:
        return 0
    headroom = WATCHLIST_CAP - len(existing)
    if headroom <= 0:
        return 0
    cleaned = cleaned[:headroom]
    WatchlistItem.objects.bulk_create(
        [WatchlistItem(watchlist=wl, ticker=t) for t in cleaned],
        ignore_conflicts=True,
    )
    return len(cleaned)
