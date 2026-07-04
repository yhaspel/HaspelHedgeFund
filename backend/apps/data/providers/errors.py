from __future__ import annotations


class OwnershipNotEntitled(RuntimeError):
    """Raised when the FMP plan is not entitled to ownership endpoints.

    The resolver catches this and falls back to SEC EDGAR.
    """


class ProviderOffline(RuntimeError):
    """Raised at the provider HTTP seam when ``settings.OFFLINE_MODE`` is on
    (P4-OFF R2/L1).

    Providers still *construct* under offline mode (the run path needs a
    provider object and the DB-first read logic serves persisted rows without
    touching the network); only an actual outbound request raises this. Callers
    that must not 500 catch it and serve the last-persisted DB rows, flagged
    ``stale``. Subclasses ``RuntimeError`` so the existing missing-key
    ``except RuntimeError`` degrade paths also treat offline as "no live data".
    """

