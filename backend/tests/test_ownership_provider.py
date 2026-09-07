"""P4 13F ownership: FMP entitlement probe, resolver, and the per-user factory
cache.

WAVE-3 P2 deleted the SEC EDGAR *bulk* 13F path (data-set ingest, CUSIP map,
by-filer view, aggregation) — it was dead end-to-end. The tests that only
covered that code went with it; what remains is the live FMP-Ultimate
by-issuer lookup and the factory that builds it.

All network access is mocked via ``httpx.MockTransport`` (no live calls).
"""

from __future__ import annotations

import datetime as dt

import httpx
import pytest

from apps.data.interfaces import IssuerOwnershipSummary
from apps.data.providers.errors import OwnershipNotEntitled
from apps.data.providers.fmp import FmpProvider
from apps.data.providers.ownership import OwnershipResolver

# ---------------------------------------------------------------------------
# FMP entitlement probe + by-issuer parse.
# ---------------------------------------------------------------------------


def _fmp_with_status(status: int, payload=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if payload is not None and status == 200:
            return httpx.Response(200, json=payload)
        return httpx.Response(status, json={})

    return FmpProvider(api_key="k", http=httpx.Client(
        transport=httpx.MockTransport(handler)))


@pytest.mark.django_db
@pytest.mark.parametrize("status", [401, 402, 403])
def test_fmp_issuer_probe_raises_not_entitled(status):
    with pytest.raises(OwnershipNotEntitled):
        _fmp_with_status(status).get_issuer_ownership(
            "AAPL", as_of=dt.date(2024, 3, 1))


@pytest.mark.django_db
def test_fmp_issuer_ownership_200_parses():
    payload = [{
        "date": "2023-12-31", "investorsHolding": 1200,
        "totalInvested": 5_000_000, "numberOf13Fshares": 9000,
        "ownershipPercent": 61.5,
    }]
    summ = _fmp_with_status(200, payload).get_issuer_ownership(
        "AAPL", as_of=dt.date(2024, 3, 1))
    assert isinstance(summ, IssuerOwnershipSummary)
    assert summ.num_holders == 1200
    assert summ.ownership_pct == 61.5
    assert summ.period_end == dt.date(2023, 12, 31)
    assert summ.source == "fmp"


# ---------------------------------------------------------------------------
# Resolver routing (FMP-if-entitled, else nothing).
# ---------------------------------------------------------------------------


class _StubFmp:
    def __init__(self, entitled):
        self.entitled = entitled

    def get_issuer_ownership(self, ticker, *, as_of):
        if not self.entitled:
            raise OwnershipNotEntitled("nope")
        return "FMP"


@pytest.mark.django_db
def test_resolver_routes_to_fmp_when_entitled():
    r = OwnershipResolver(fmp=_StubFmp(True))
    assert r.get_issuer_ownership("AAPL", as_of=dt.date(2024, 1, 1)) == "FMP"


@pytest.mark.django_db
def test_resolver_returns_none_when_not_entitled():
    r = OwnershipResolver(fmp=_StubFmp(False))
    assert r.get_issuer_ownership("AAPL", as_of=dt.date(2024, 1, 1)) is None


@pytest.mark.django_db
def test_resolver_returns_none_without_fmp():
    r = OwnershipResolver(fmp=None)
    assert r.get_issuer_ownership("AAPL", as_of=dt.date(2024, 1, 1)) is None


# ---------------------------------------------------------------------------
# Factory: per-user lru_cache + the no-key build.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_factory_ownership_cache_and_reset(monkeypatch):
    from apps.data.providers import factory

    factory._reset_caches_for_tests()
    monkeypatch.setattr(factory, "_resolve_data_key",
                        lambda *a, **k: ("key-abc", "user"))
    a = factory.get_ownership_provider(user=None)
    b = factory.get_ownership_provider(user=None)
    assert a is b
    assert a._fmp is not None  # a resolvable key -> FMP wired in
    factory._reset_caches_for_tests()
    c = factory.get_ownership_provider(user=None)
    assert c is not a


@pytest.mark.django_db
def test_factory_yields_an_empty_resolver_when_no_key(monkeypatch):
    from apps.data.providers import factory

    factory._reset_caches_for_tests()

    def _raise(*a, **k):
        raise RuntimeError("no key")

    monkeypatch.setattr(factory, "_resolve_data_key", _raise)
    resolver = factory.get_ownership_provider(user=None)
    assert resolver._fmp is None
    # No EDGAR fallback any more: every lookup is None, and the reason is
    # carried on the resolver for the caller to surface.
    assert resolver.get_issuer_ownership("AAPL", as_of=dt.date(2026, 9, 7)) is None
    assert "FMP Ultimate" in resolver.not_entitled_reason
    factory._reset_caches_for_tests()
