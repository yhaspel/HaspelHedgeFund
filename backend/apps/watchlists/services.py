"""Named-watchlist services (P3b).

The single-list helpers the rest of the app calls (`add_tickers_to_watchlist`,
`watchlist_tickers`) default to the user's *default* list, preserving the
behaviour of the old `screener.services` helpers they replace.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .models import Watchlist, WatchlistTicker

# Per-list ticker cap (kept in lockstep with the views) and a per-user cap on
# the number of lists so a single user can't create thousands of watchlists.
WATCHLIST_CAP = 100
MAX_WATCHLISTS_PER_USER = 50


def get_default_watchlist(user: Any) -> Watchlist:
    """Return the user's default list, creating/promoting one if needed."""
    wl = Watchlist.objects.filter(user=user, is_default=True).first()
    if wl is not None:
        return wl
    # No default flagged yet: promote the oldest existing list, else create one.
    existing = Watchlist.objects.filter(user=user).order_by("created_at").first()
    if existing is not None:
        existing.is_default = True
        existing.save(update_fields=["is_default"])
        return existing
    return Watchlist.objects.create(user=user, name="My Watchlist", is_default=True)


def resolve_watchlist(user: Any, ref: str | int) -> Watchlist:
    """Resolve a URL ref (``"default"`` or a numeric id) to one of the user's
    lists. Raises ``Watchlist.DoesNotExist`` if not owned/found."""
    if isinstance(ref, str) and ref == "default":
        return get_default_watchlist(user)
    try:
        pk = int(ref)
    except (TypeError, ValueError) as exc:
        raise Watchlist.DoesNotExist(str(ref)) from exc
    return Watchlist.objects.get(user=user, pk=pk)


def watchlist_tickers(user: Any) -> set[str]:
    """Upper-cased union of every ticker across all of the user's lists.

    Used by the screener to flag ``in_watchlist`` on result rows.
    """
    return {
        t.upper()
        for t in WatchlistTicker.objects.filter(
            watchlist__user=user
        ).values_list("ticker", flat=True)
    }


def add_tickers_to_watchlist(
    user: Any, tickers: Iterable[str], watchlist: Watchlist | None = None
) -> int:
    """Idempotently merge ``tickers`` into a list (default list if unspecified).

    Upper-cases, de-dupes against existing items, respects the per-list cap.
    Returns the number actually added. Safe with an empty/falsy ``tickers``.
    """
    if not tickers:
        return 0
    wl = watchlist or get_default_watchlist(user)
    existing = {t.upper() for t in wl.tickers.values_list("ticker", flat=True)}
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
    WatchlistTicker.objects.bulk_create(
        [WatchlistTicker(watchlist=wl, ticker=t) for t in cleaned],
        ignore_conflicts=True,
    )
    return len(cleaned)
