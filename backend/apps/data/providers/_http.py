"""Offline-aware httpx client factory for the data providers (P4-OFF).

Every external provider builds its client with ``make_client(...)`` instead of
``httpx.Client(...)`` directly. When ``settings.OFFLINE_MODE`` is on, the
returned object refuses every outbound request with :class:`ProviderOffline`,
so no external HTTP can leave the machine at L1 — the socket-guard guarantee.

The fence is at the *request* seam, not at provider construction: providers must
still instantiate so the council run path (which builds provider objects eagerly
and then reads DB-first) keeps working. Only a call that would actually hit the
network raises.
"""
from __future__ import annotations

from typing import Any

import httpx
from django.conf import settings

from .errors import ProviderOffline


class _OfflineClient:
    """Stand-in for ``httpx.Client`` that blocks all requests in OFFLINE_MODE."""

    def _blocked(self, *args: Any, **kwargs: Any) -> Any:
        raise ProviderOffline(
            "External data providers are disabled in OFFLINE_MODE (L1). "
            "Serve the last-persisted DB rows instead of fetching live."
        )

    # httpx.Client's request surface used by the providers.
    get = post = put = patch = delete = head = request = stream = _blocked

    def __enter__(self) -> _OfflineClient:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def close(self) -> None:  # pragma: no cover — no resources to release
        pass


def make_client(**kwargs: Any) -> httpx.Client:
    """Return an ``httpx.Client`` — or an offline stand-in that raises
    :class:`ProviderOffline` on any request — depending on ``OFFLINE_MODE``.

    ``OFFLINE_MODE`` is a process-level env flag (read once at settings load and
    constant for the process), so evaluating it at construction time is correct.
    """
    if getattr(settings, "OFFLINE_MODE", False):
        return _OfflineClient()  # type: ignore[return-value]
    return httpx.Client(**kwargs)
