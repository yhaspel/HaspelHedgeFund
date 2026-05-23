"""Per-user screener services (saved screens + watchlist)."""
from __future__ import annotations

from typing import Any

from .models import Watchlist


def get_or_create_watchlist(user: Any) -> Watchlist:
    wl, _ = Watchlist.objects.get_or_create(user=user)
    return wl
