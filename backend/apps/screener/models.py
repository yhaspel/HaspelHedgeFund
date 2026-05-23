"""Models for the market screener (P3 prereq 3).

Three per-user models live here:

* ``SavedScreen``  — a named custom filter set the user can reload.
* ``Watchlist``    — one row per user (we ship v1 with a single watchlist).
* ``WatchlistItem``— a starred ticker on the user's watchlist.

The market screener is *today*-data only: it must never be imported from a
backtest / point-in-time path. The PIT regression test in
``tests/test_screener_pit.py`` asserts the import boundary.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models


ASSET_CLASS_EQUITY = "equity"
ASSET_CLASS_ETF = "etf"
ASSET_CLASS_ALL = "all"
ASSET_CLASS_CHOICES = [
    (ASSET_CLASS_EQUITY, "Equity"),
    (ASSET_CLASS_ETF, "ETF"),
    (ASSET_CLASS_ALL, "Equity + ETF"),
]


class SavedScreen(models.Model):
    """A user's named custom screen.

    The ``filters`` field is the *normalized filter set* — exactly the shape
    the ``/api/screener/run/`` endpoint expects. The serializer validates
    ``filters`` against ``FIELD_REGISTRY`` so a malformed saved screen can
    never reach the pipeline.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_screens",
    )
    name = models.CharField(max_length=120)
    asset_class = models.CharField(
        max_length=8, choices=ASSET_CLASS_CHOICES, default=ASSET_CLASS_EQUITY
    )
    filters = models.JSONField(default=dict)
    sort = models.JSONField(default=dict)
    based_on = models.CharField(max_length=32, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "name"], name="uniq_saved_screen_name_per_user"
            ),
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} (user={self.user_id})"


class Watchlist(models.Model):
    """A user's single watchlist (v1 ships one-per-user).

    Multiple named watchlists are a planned follow-up; switching the
    ``OneToOneField`` to a ``ForeignKey`` is the only code change required.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="watchlist",
    )
    name = models.CharField(max_length=120, default="My Watchlist")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Watchlist(user={self.user_id})"


class WatchlistItem(models.Model):
    watchlist = models.ForeignKey(
        Watchlist, on_delete=models.CASCADE, related_name="items"
    )
    ticker = models.CharField(max_length=16, db_index=True)
    note = models.CharField(max_length=240, blank=True, default="")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["watchlist", "ticker"], name="uniq_watchlist_ticker"
            ),
        ]
        ordering = ["-added_at"]

    def __str__(self) -> str:
        return f"{self.ticker} (wl={self.watchlist_id})"
