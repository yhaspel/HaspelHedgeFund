"""Ownership (13F) resolver: prefer FMP (if entitled), else SEC EDGAR.

The resolver implements the OwnershipProvider protocol.  EDGAR has no
efficient by-issuer query, so the by-issuer path reads aggregated
IssuerOwnershipSnapshot rows produced by the bulk-ingest command (WS-2),
while the by-filer path falls through to EdgarProvider.get_filer_portfolio.
"""

from __future__ import annotations

from datetime import date

import httpx

from apps.data.interfaces import (
    FilerPortfolio,
    HolderStake,
    IssuerOwnershipSummary,
)
from apps.data.providers.errors import OwnershipNotEntitled, ProviderOffline


class OwnershipResolver:
    """Prefer FMP (if entitled), else fall back to SEC EDGAR."""

    name = "ownership"

    def __init__(self, *, fmp, edgar):
        self._fmp = fmp
        self._edgar = edgar

    def get_issuer_ownership(
        self, ticker: str, *, as_of: date
    ) -> IssuerOwnershipSummary | None:
        if self._fmp is not None:
            try:
                return self._fmp.get_issuer_ownership(ticker, as_of=as_of)
            except (OwnershipNotEntitled, httpx.HTTPError, ProviderOffline):
                # Not entitled, a wrong/placeholder slug (404), any transport
                # error, or OFFLINE_MODE: degrade to EDGAR DB snapshots rather
                # than hard-failing the caller.
                pass
        # EDGAR has no efficient by-issuer query; read aggregated snapshots.
        return _edgar_issuer_from_snapshots(ticker, as_of=as_of)

    def get_filer_portfolio(
        self, filer_cik: str, *, as_of: date
    ) -> FilerPortfolio | None:
        if self._fmp is not None:
            try:
                return self._fmp.get_filer_portfolio(filer_cik, as_of=as_of)
            except (OwnershipNotEntitled, httpx.HTTPError, ProviderOffline):
                # Not entitled / wrong slug / transport error / offline → fall back.
                pass
        try:
            return self._edgar.get_filer_portfolio(filer_cik, as_of=as_of)
        except (httpx.HTTPError, ProviderOffline):
            # The by-filer path is cached-first-then-HTTP with no DB-only fallback,
            # so on a transport error / OFFLINE_MODE cache miss degrade to None
            # (the return type already allows it) rather than hard-failing.
            return None


def _edgar_issuer_from_snapshots(
    ticker: str, *, as_of: date
) -> IssuerOwnershipSummary | None:
    """Read the latest aggregated IssuerOwnershipSnapshot(source=edgar).

    PIT: as_of_date <= as_of, newest first.
    """
    from apps.data.models import IssuerOwnershipSnapshot

    snap = (
        IssuerOwnershipSnapshot.objects.filter(
            ticker=ticker.upper(),
            source="edgar",
            as_of_date__lte=as_of,
        )
        .order_by("-as_of_date", "-period_end")
        .first()
    )
    if snap is None:
        return None
    return _summary_from_snapshot(snap)


def _summary_from_snapshot(snap) -> IssuerOwnershipSummary:
    top = [
        HolderStake(
            filer_cik=str(h.get("filer_cik", "")),
            filer_name=h.get("filer_name", ""),
            shares=int(h.get("shares", 0)),
            value_usd=int(h.get("value_usd", 0)),
            pct_of_portfolio=h.get("pct_of_portfolio"),
        )
        for h in (snap.top_holders or [])
    ]
    return IssuerOwnershipSummary(
        ticker=snap.ticker,
        period_end=snap.period_end,
        as_of=snap.as_of_date,
        num_holders=snap.num_holders,
        total_shares=snap.total_shares,
        total_value_usd=snap.total_value_usd,
        institutional_ownership_pct=snap.institutional_ownership_pct,
        ownership_pct=snap.ownership_pct,
        qoq_value_change_pct=snap.qoq_value_change_pct,
        top_holders=top,
        new_positions=list(snap.new_positions or []),
        closed_positions=list(snap.closed_positions or []),
        source=snap.source,
    )


def resolve_cusip(ticker: str) -> str | None:
    """ticker -> cusip via the CusipTicker cache."""
    from apps.data.models import CusipTicker

    row = CusipTicker.objects.filter(ticker=ticker.upper()).first()
    return row.cusip if row else None


def cusip_to_ticker(cusip: str) -> str | None:
    """cusip -> ticker via the CusipTicker cache."""
    from apps.data.models import CusipTicker

    if not cusip:
        return None
    row = CusipTicker.objects.filter(cusip=cusip).first()
    return row.ticker if row else None


def cache_cusip(cusip: str, ticker: str, issuer_name: str = "") -> None:
    """Upsert a CUSIP<->ticker mapping."""
    from apps.data.models import CusipTicker

    if not cusip or not ticker:
        return
    CusipTicker.objects.update_or_create(
        cusip=cusip,
        defaults={"ticker": ticker.upper(), "issuer_name": issuer_name},
    )
