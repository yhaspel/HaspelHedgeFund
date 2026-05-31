from __future__ import annotations


class OwnershipNotEntitled(RuntimeError):
    """Raised when the FMP plan is not entitled to ownership endpoints.

    The resolver catches this and falls back to SEC EDGAR.
    """
