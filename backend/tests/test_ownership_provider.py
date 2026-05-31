"""P4 13F ownership: EDGAR by-filer parse, FMP probe, resolver, no-cheat,
aggregation, and the per-user factory cache.

All network access is mocked via ``httpx.MockTransport`` (no live calls in
this suite). The EDGAR parse tests replay captured INFORMATION TABLE XML
from ``tests/fixtures/``; ``form13f_infotable_scion.xml`` is a real filing
(Scion Asset Management, CIK 1649339, 2022Q3 13F-HR) fetched once from SEC
EDGAR and replayed offline thereafter.
"""

from __future__ import annotations

import datetime as dt
import os

import httpx
import pytest

from apps.data.interfaces import FilerPortfolio, IssuerOwnershipSummary
from apps.data.models import InstitutionalHolding, IssuerOwnershipSnapshot
from apps.data.providers.edgar import EdgarProvider
from apps.data.providers.errors import OwnershipNotEntitled
from apps.data.providers.fmp import FmpProvider
from apps.data.providers.ownership import OwnershipResolver

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_infotable(name: str = "form13f_infotable.xml") -> str:
    with open(os.path.join(_FIXTURES, name)) as fh:
        return fh.read()


def _edgar_with_filing(
    filed: str, *, period: str | None = None, xml_name: str = "form13f_infotable.xml"
):
    """An EdgarProvider whose submissions/index/info-table GETs are mocked.

    Serves one 13F-HR filed on ``filed`` for period ``period`` (defaults to
    ``filed``), backed by the named INFORMATION TABLE fixture.
    """
    info_xml = _load_infotable(xml_name)
    period = period or filed

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "submissions/CIK" in url:
            return httpx.Response(200, json={
                "name": "TEST CAPITAL MGMT",
                "filings": {"recent": {
                    "form": ["13F-HR"],
                    "filingDate": [filed],
                    "accessionNumber": ["0001234567-24-000001"],
                    "periodOfReport": [period],
                }},
            })
        if url.endswith("index.json"):
            return httpx.Response(200, json={"directory": {"item": [
                {"name": "primary_doc.xml"},
                {"name": "form13fInfoTable.xml"},
            ]}})
        if url.endswith("form13fInfoTable.xml"):
            return httpx.Response(200, text=info_xml)
        return httpx.Response(404)

    return EdgarProvider(http=httpx.Client(transport=httpx.MockTransport(handler)))


# ---------------------------------------------------------------------------
# EDGAR by-filer parse.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_edgar_filer_portfolio_parse_post_2023():
    """Post-boundary filing: values are whole dollars (no x1000), with
    correct shares and value-weighted portfolio weights."""
    prov = _edgar_with_filing("2024-02-14", period="2023-12-31")
    pf = prov.get_filer_portfolio("9990001", as_of=dt.date(2024, 3, 1))
    assert isinstance(pf, FilerPortfolio)
    assert len(pf.holdings) == 2
    assert pf.total_value_usd == 4_000_000
    msft = next(h for h in pf.holdings if h.issuer_cusip == "594918104")
    assert msft.shares == 8000
    assert msft.value_usd == 3_000_000
    assert round(msft.weight_pct, 1) == 75.0
    assert InstitutionalHolding.objects.filter(source="edgar").count() == 2


@pytest.mark.django_db
def test_edgar_filer_portfolio_parses_real_scion_xml():
    """A captured real SEC INFORMATION TABLE (Scion 2022Q3) parses cleanly.

    Asserted on structural invariants that hold for any valid filing so the
    test stays robust to the exact captured holdings.
    """
    # Filed 2022-11-14 (pre-boundary) so every value is x1000-normalized.
    prov = _edgar_with_filing(
        "2022-11-14", period="2022-09-30", xml_name="form13f_infotable_scion.xml"
    )
    pf = prov.get_filer_portfolio("1649339", as_of=dt.date(2022, 12, 31))
    assert isinstance(pf, FilerPortfolio)
    assert pf.period_end == dt.date(2022, 9, 30)
    assert pf.source == "edgar"
    assert len(pf.holdings) == 6
    for h in pf.holdings:
        assert h.shares > 0
        assert h.value_usd > 0
        assert h.issuer_cusip
        assert h.value_usd % 1000 == 0  # x1000 normalization (pre-boundary)
    assert pf.total_value_usd == sum(h.value_usd for h in pf.holdings)
    assert sum(h.weight_pct for h in pf.holdings) == pytest.approx(100.0, abs=0.5)


@pytest.mark.django_db
def test_edgar_parse_is_namespace_robust():
    """The parser handles a bare <informationTable> with no XML namespace."""
    prov = _edgar_with_filing(
        "2024-02-14", period="2023-12-31", xml_name="form13f_infotable_nons.xml"
    )
    pf = prov.get_filer_portfolio("9990003", as_of=dt.date(2024, 3, 1))
    assert pf is not None
    assert {h.issuer_cusip for h in pf.holdings} == {"037833100", "023135106"}


@pytest.mark.django_db
def test_edgar_value_unit_boundary_pre_2023():
    """Pre-boundary filing (< 2023-01-03): <value> in $1000s -> x1000."""
    prov = _edgar_with_filing("2022-11-14", period="2022-09-30")
    pf = prov.get_filer_portfolio("9990002", as_of=dt.date(2022, 12, 1))
    aapl = next(h for h in pf.holdings if h.issuer_cusip == "037833100")
    assert aapl.value_usd == 1_000_000 * 1000  # $1000s -> dollars


@pytest.mark.django_db
def test_edgar_amendment_supersedes_and_pit():
    """Cached-first by-filer read returns the newest pre-as_of quarter only;
    a future-filed amendment/quarter must not leak (no-cheat)."""
    InstitutionalHolding.objects.create(
        filer_cik="42", filer_name="X", issuer_cusip="037833100",
        issuer_name="APPLE", ticker="AAPL", period_end=dt.date(2023, 9, 30),
        filed_at=dt.date(2023, 11, 1), shares=10, value_usd=100, source="edgar",
    )
    InstitutionalHolding.objects.create(
        filer_cik="42", filer_name="X", issuer_cusip="037833100",
        issuer_name="APPLE", ticker="AAPL", period_end=dt.date(2024, 3, 31),
        filed_at=dt.date(2024, 5, 1), shares=999, value_usd=999, source="edgar",
    )
    prov = EdgarProvider(http=httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(404))))
    pf = prov.get_filer_portfolio("42", as_of=dt.date(2024, 1, 1))
    assert pf.period_end == dt.date(2023, 9, 30)
    assert pf.holdings[0].shares == 10


# ---------------------------------------------------------------------------
# FMP entitlement probe.
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
@pytest.mark.parametrize("status", [401, 402, 403])
def test_fmp_filer_probe_raises_not_entitled(status):
    with pytest.raises(OwnershipNotEntitled):
        _fmp_with_status(status).get_filer_portfolio(
            "1649339", as_of=dt.date(2024, 3, 1))


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
    assert IssuerOwnershipSnapshot.objects.filter(source="fmp").count() == 1


@pytest.mark.django_db
def test_fmp_filer_portfolio_200_parses():
    payload = [
        {"date": "2023-12-31", "investorName": "BRK", "cusip": "037833100",
         "securityName": "APPLE INC", "symbol": "aapl",
         "marketValue": 1_000_000, "sharesNumber": 5000},
        {"date": "2023-12-31", "investorName": "BRK", "cusip": "023135106",
         "securityName": "AMAZON", "symbol": "amzn",
         "marketValue": 500_000, "sharesNumber": 2000},
    ]
    pf = _fmp_with_status(200, payload).get_filer_portfolio(
        "1067983", as_of=dt.date(2024, 3, 1))
    assert isinstance(pf, FilerPortfolio)
    assert pf.source == "fmp"
    assert pf.total_value_usd == 1_500_000
    assert {h.ticker for h in pf.holdings} == {"AAPL", "AMZN"}
    aapl = next(h for h in pf.holdings if h.ticker == "AAPL")
    assert round(aapl.weight_pct, 4) == round(1_000_000 / 1_500_000 * 100, 4)


# ---------------------------------------------------------------------------
# Resolver routing (FMP-if-entitled, else EDGAR).
# ---------------------------------------------------------------------------


class _StubFmp:
    def __init__(self, entitled):
        self.entitled = entitled

    def get_issuer_ownership(self, ticker, *, as_of):
        if not self.entitled:
            raise OwnershipNotEntitled("nope")
        return "FMP_ISSUER"

    def get_filer_portfolio(self, filer_cik, *, as_of):
        if not self.entitled:
            raise OwnershipNotEntitled("nope")
        return "FMP"


class _StubEdgar:
    def get_filer_portfolio(self, filer_cik, *, as_of):
        return "EDGAR"


@pytest.mark.django_db
def test_resolver_routes_to_fmp_when_entitled():
    r = OwnershipResolver(fmp=_StubFmp(True), edgar=_StubEdgar())
    assert r.get_filer_portfolio("1", as_of=dt.date(2024, 1, 1)) == "FMP"


@pytest.mark.django_db
def test_resolver_falls_back_to_edgar_on_not_entitled():
    r = OwnershipResolver(fmp=_StubFmp(False), edgar=_StubEdgar())
    assert r.get_filer_portfolio("1", as_of=dt.date(2024, 1, 1)) == "EDGAR"


@pytest.mark.django_db
def test_resolver_edgar_only_when_no_fmp():
    r = OwnershipResolver(fmp=None, edgar=_StubEdgar())
    assert r.get_filer_portfolio("1", as_of=dt.date(2024, 1, 1)) == "EDGAR"


@pytest.mark.django_db
def test_resolver_issuer_reads_edgar_snapshot():
    IssuerOwnershipSnapshot.objects.create(
        ticker="AAPL", period_end=dt.date(2023, 12, 31),
        as_of_date=dt.date(2024, 2, 14), num_holders=5, total_shares=100,
        total_value_usd=1000, source="edgar",
        top_holders=[{"filer_cik": "1", "filer_name": "Z", "shares": 1,
                      "value_usd": 1}],
    )
    r = OwnershipResolver(fmp=None, edgar=_StubEdgar())
    summ = r.get_issuer_ownership("AAPL", as_of=dt.date(2024, 3, 1))
    assert summ is not None
    assert summ.num_holders == 5
    assert summ.top_holders[0].filer_name == "Z"


@pytest.mark.django_db
def test_resolver_issuer_pit_excludes_future_snapshot():
    IssuerOwnershipSnapshot.objects.create(
        ticker="AAPL", period_end=dt.date(2024, 3, 31),
        as_of_date=dt.date(2024, 5, 15), num_holders=99, total_shares=1,
        total_value_usd=1, source="edgar",
    )
    r = OwnershipResolver(fmp=None, edgar=_StubEdgar())
    assert r.get_issuer_ownership("AAPL", as_of=dt.date(2024, 4, 1)) is None


# ---------------------------------------------------------------------------
# Aggregation (the ingest command's snapshot builder).
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_aggregation_builds_snapshots_with_qoq_and_deltas():
    """Seeded InstitutionalHolding rows -> IssuerOwnershipSnapshot with the
    right rollups, QoQ delta, and new/closed-position sets vs. the prior
    quarter."""
    from apps.data.management.commands.ingest_13f_datasets import Command

    prior = dt.date(2023, 9, 30)
    curr = dt.date(2023, 12, 31)
    filed = dt.date(2024, 2, 14)

    def _hold(cik, cusip, ticker, period, value, shares):
        InstitutionalHolding.objects.create(
            filer_cik=cik, filer_name=f"FILER {cik}", issuer_cusip=cusip,
            issuer_name=ticker, ticker=ticker, period_end=period,
            filed_at=filed, shares=shares, value_usd=value, put_call="",
            source="edgar",
        )

    # Prior quarter: AAPL held by filer A only.
    _hold("A", "037833100", "AAPL", prior, 800_000, 4000)
    # Current quarter: AAPL held by A + B (B is new); AMZN newly opened by A.
    _hold("A", "037833100", "AAPL", curr, 600_000, 3000)
    _hold("B", "037833100", "AAPL", curr, 400_000, 2000)
    _hold("A", "023135106", "AMZN", curr, 500_000, 2000)

    Command()._aggregate()

    aapl = IssuerOwnershipSnapshot.objects.get(ticker="AAPL", period_end=curr)
    assert aapl.num_holders == 2
    assert aapl.total_value_usd == 1_000_000
    assert aapl.total_shares == 5000
    assert [h["filer_cik"] for h in aapl.top_holders] == ["A", "B"]
    # QoQ: (1,000,000 - 800,000) / 800,000 = +25%.
    assert aapl.qoq_value_change_pct == pytest.approx(25.0)
    assert aapl.new_positions == ["B"]
    assert aapl.closed_positions == []

    amzn = IssuerOwnershipSnapshot.objects.get(ticker="AMZN", period_end=curr)
    assert amzn.num_holders == 1
    assert amzn.total_value_usd == 500_000
    # No prior AMZN quarter exists, so there is nothing to diff against and the
    # delta sets stay empty (new/closed are only computed vs. a prior quarter).
    assert amzn.new_positions == []
    assert amzn.closed_positions == []


# ---------------------------------------------------------------------------
# Factory: per-user lru_cache + EDGAR-only build.
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
def test_factory_edgar_only_when_no_key(monkeypatch):
    from apps.data.providers import factory

    factory._reset_caches_for_tests()

    def _raise(*a, **k):
        raise RuntimeError("no key")

    monkeypatch.setattr(factory, "_resolve_data_key", _raise)
    resolver = factory.get_ownership_provider(user=None)
    assert resolver._fmp is None
    assert resolver._edgar is not None
    factory._reset_caches_for_tests()
