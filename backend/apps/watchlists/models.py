"""Named watchlists (P3b).

Replaces the single-per-user ``screener.Watchlist`` with multiple named lists
so the scheduler can target distinct ticker sets ("Quality compounders",
"Macro plays"). The old screener rows are data-migrated into a default
"My Watchlist" per user (see ``screener/migrations/0002``).

Each user has exactly one ``is_default`` list — the one the dashboard card,
the screener "add", and the investor-profile auto-seed write to. The default
is enforced by a conditional unique constraint and resolved/created lazily by
``services.get_default_watchlist``.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models


class Watchlist(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="watchlists",
    )
    name = models.CharField(max_length=120, default="My Watchlist")
    # Exactly one default per user (the list legacy single-watchlist consumers
    # write to). Enforced below by a partial unique constraint.
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "name"], name="uniq_watchlist_name_per_user"
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(is_default=True),
                name="uniq_default_watchlist_per_user",
            ),
        ]
        ordering = ["-is_default", "name"]

    def __str__(self) -> str:
        return f"{self.name} (user={self.user_id})"


class WatchlistTicker(models.Model):
    watchlist = models.ForeignKey(
        Watchlist, on_delete=models.CASCADE, related_name="tickers"
    )
    ticker = models.CharField(max_length=16, db_index=True)
    note = models.CharField(max_length=240, blank=True, default="")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["watchlist", "ticker"], name="uniq_named_watchlist_ticker"
            ),
        ]
        ordering = ["-added_at"]

    def __str__(self) -> str:
        return f"{self.ticker} (wl={self.watchlist_id})"
