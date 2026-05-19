"""Borrow availability — stub provider for P2e.

Real broker borrow API (IBKR Web API) lands in P3a. Until then, a fixed
HTB blacklist exercises the borrow-veto code path so the council short
side has a meaningful "this short was dropped because the shares aren't
locatable" failure mode.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

# Hard-to-borrow tickers — deterministic ~5% of a typical large-cap universe.
# These names are flagged not-locatable so the RM/Constructor have to drop
# any short attempt on them with a recorded reason. Picked from real HTB lists
# in 2024-2025 (small floats / heavy short interest / meme history).
HTB_TICKERS: set[str] = {
    "GME", "AMC", "BBBY", "CLOV", "MULN",
    "PROG", "TRKA", "FFIE", "PHUN", "ATER",
}


@dataclass
class BorrowInfo:
    ticker: str
    as_of_date: date
    is_locatable: bool
    fee_pct_annual: Decimal
    source: str = "stub"


class BorrowProvider(Protocol):
    def quote(self, ticker: str, as_of: date) -> BorrowInfo: ...


class StubBorrowProvider:
    """Deterministic: 10 HTB names → not locatable; everything else → locatable
    at a 1% annual fee. Result is also persisted as a BorrowQuote row so the
    UI can show why a short was dropped."""

    def quote(self, ticker: str, as_of: date) -> BorrowInfo:
        t = ticker.upper()
        if t in HTB_TICKERS:
            return BorrowInfo(t, as_of, is_locatable=False, fee_pct_annual=Decimal("0.50"))
        return BorrowInfo(t, as_of, is_locatable=True, fee_pct_annual=Decimal("0.01"))

    def persist(self, info: BorrowInfo) -> None:
        from .models import BorrowQuote
        BorrowQuote.objects.update_or_create(
            ticker=info.ticker, as_of_date=info.as_of_date, source=info.source,
            defaults={
                "is_locatable": info.is_locatable,
                "fee_pct_annual": info.fee_pct_annual,
            },
        )
