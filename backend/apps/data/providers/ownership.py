"""Ownership (13F) resolver: FMP institutional ownership, or nothing.

WAVE-3 P2 item 2 deleted the SEC EDGAR *bulk* 13F path this used to fall back
to. It was dead end-to-end: SEC renamed the Form 13F data sets in 2024 (the
quarterly beat 404'd on every run since), nothing in the codebase ever wrote a
``CusipTicker`` row, so every ingested holding landed with ``ticker=""`` and the
aggregation — which excludes blank tickers — built zero
``IssuerOwnershipSnapshot`` rows. The by-filer path had no callers and no UI.

What remains is the single live path: FMP's Ultimate-entitled by-issuer
summary, consumed by ``hedgefund_agents/analytical/fundamentals.py``. Without
that entitlement the resolver returns ``None`` and the fundamentals node says
so in its output.
"""

from __future__ import annotations

from datetime import date

import httpx

from apps.data.interfaces import IssuerOwnershipSummary
from apps.data.providers.errors import OwnershipNotEntitled, ProviderOffline

#: Why ``get_issuer_ownership`` came back empty. Surfaced verbatim by callers
#: so the user sees a plan requirement, not a silent blank.
NOT_ENTITLED_REASON = (
    "Institutional ownership (13F) requires an FMP Ultimate-entitled key; "
    "no ownership data is available."
)


def _record_provider_outage(provider: str, exc: Exception) -> None:
    """Best-effort: count a data-provider transport failure toward the operator
    outage alert. Lazy import keeps apps.data free of an apps.notifications
    dependency at module load, and a failure here must never break the resolver."""
    try:
        from apps.notifications.operator import record_provider_failure

        record_provider_failure(provider, detail=f"{type(exc).__name__}: {exc}")
    except Exception:  # noqa: BLE001
        pass


class OwnershipResolver:
    """FMP by-issuer institutional ownership (Ultimate tier), or ``None``."""

    name = "ownership"

    #: Re-exported so callers can explain an empty result without re-deriving it.
    not_entitled_reason = NOT_ENTITLED_REASON

    def __init__(self, *, fmp, edgar=None):
        self._fmp = fmp
        # Kept for factory-signature compatibility; the EDGAR bulk ownership
        # path no longer exists (the filings provider is unrelated and live).
        self._edgar = edgar

    def get_issuer_ownership(
        self, ticker: str, *, as_of: date
    ) -> IssuerOwnershipSummary | None:
        if self._fmp is None:
            return None
        try:
            return self._fmp.get_issuer_ownership(ticker, as_of=as_of)
        except httpx.TransportError as exc:
            # A genuine connect/read/timeout outage (not a 404 or entitlement
            # issue). Count it so the operator is alerted if one provider keeps
            # failing (P5-SH WS2.2).
            _record_provider_outage("fmp-ownership", exc)
        except (OwnershipNotEntitled, httpx.HTTPError, ProviderOffline):
            # Not entitled, a wrong/placeholder slug (404), any other HTTP
            # status error, or OFFLINE_MODE: degrade to None rather than
            # hard-failing the caller.
            pass
        return None
