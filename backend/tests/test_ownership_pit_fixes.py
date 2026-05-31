"""Post-review hardening tests for the 13F ownership layer (P4 prereq).

Covers the fixes applied after the adversarial review:
  - FMP by-issuer / by-filer paths are point-in-time gated (no look-ahead on
    an entitled Ultimate key) and stamp the *real* filing date, not `as_of`.
  - The resolver degrades to EDGAR on any FMP transport error (e.g. a wrong
    endpoint slug → 404), not just OwnershipNotEntitled.
  - EDGAR recovers a 13F report period when periodOfReport is missing.
  - EDGAR skips PRN (debt-principal) rows so they never enter the long-equity
    aggregate.
  - The cached by-filer read re-applies filed_at <= as_of, excluding a
    same-period 13F-HR/A amendment filed after as_of.

No live network: FMP is a tiny fake httpx client; EDGAR is driven by canned
submissions/index/XML responses or by seeded rows.
"""

from __future__ import annotations

import datetime as dt

import httpx
import pytest

from apps.data.providers.edgar import EdgarProvider
from apps.data.providers.fmp import FmpProvider
from apps.data.providers.ownership import OwnershipResolver


class _FakeHttp:
    """httpx.Client stand-in: returns queued responses by call order (last sticky)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self._i = 0

    def get(self, url, **kwargs):
        resp = (
            self._responses[self._i]
            if self._i < len(self._responses)
            else self._responses[-1]
        )
        self._i += 1
        return resp


class _Resp:
    """Minimal httpx.Response stand-in."""

    def __init__(self, *, status_code=200, json_data=None, text_data=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else []
        self.text = text_data

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("e", request=None, response=None)


def _info_table_xml(rows) -> str:
    """Build a namespaced 13F INFORMATION TABLE. Each row is a dict with
    name/cusip/value/shares/ssh_type (ssh_type 'SH' or 'PRN')."""
    body = []
    for r in rows:
        body.append(
            "<infoTable>"
            f"<nameOfIssuer>{r['name']}</nameOfIssuer>"
            f"<cusip>{r['cusip']}</cusip>"
            f"<value>{r['value']}</value>"
            "<shrsOrPrnAmt>"
            f"<sshPrnamt>{r['shares']}</sshPrnamt>"
            f"<sshPrnamtType>{r.get('ssh_type', 'SH')}</sshPrnamtType>"
            "</shrsOrPrnAmt>"
            "</infoTable>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">'
        + "".join(body)
        + "</informationTable>"
    )


# ---------------------------------------------------------------------------
# FMP point-in-time gating
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_fmp_issuer_excludes_future_period() -> None:
    """A period whose filing date (period_end+45d) is after as_of → None."""
    payload = [
        {
            "date": "2024-06-30",
            "investorsHolding": 5,
            "totalInvested": 1_000,
            "numberOf13Fshares": 10,
        }
    ]
    fmp = FmpProvider(api_key="k", http=_FakeHttp([_Resp(json_data=payload)]))
    # 2024-06-30 + 45d = 2024-08-14, which is after the query date.
    out = fmp.get_issuer_ownership("AAPL", as_of=dt.date(2024, 7, 1))
    assert out is None


@pytest.mark.django_db
def test_fmp_issuer_stamps_real_filing_date_not_as_of() -> None:
    """as_of on the summary is the derived filing date, never the caller's."""
    from apps.data.models import IssuerOwnershipSnapshot

    payload = [
        {
            "date": "2024-03-31",
            "investorsHolding": 11,
            "totalInvested": 5_000,
            "numberOf13Fshares": 100,
            "ownershipPercent": 60.0,
        }
    ]
    fmp = FmpProvider(api_key="k", http=_FakeHttp([_Resp(json_data=payload)]))
    out = fmp.get_issuer_ownership("AAPL", as_of=dt.date(2024, 9, 30))
    assert out is not None
    # period_end + 45d, NOT the 2024-09-30 query date.
    assert out.as_of == dt.date(2024, 5, 15)
    snap = IssuerOwnershipSnapshot.objects.get(ticker="AAPL", source="fmp")
    assert snap.as_of_date == dt.date(2024, 5, 15)


@pytest.mark.django_db
def test_fmp_issuer_honors_explicit_filing_date() -> None:
    """An explicit filingDate field gates and stamps the row."""
    payload = [
        {
            "date": "2024-03-31",
            "filingDate": "2024-05-10",
            "investorsHolding": 3,
            "totalInvested": 9,
            "numberOf13Fshares": 1,
        }
    ]
    # Query the day before the real filing date → not yet knowable.
    fmp = FmpProvider(api_key="k", http=_FakeHttp([_Resp(json_data=payload)]))
    assert fmp.get_issuer_ownership("AAPL", as_of=dt.date(2024, 5, 9)) is None
    # On/after the filing date → returned, stamped with the real date.
    fmp2 = FmpProvider(api_key="k", http=_FakeHttp([_Resp(json_data=payload)]))
    out = fmp2.get_issuer_ownership("AAPL", as_of=dt.date(2024, 5, 10))
    assert out is not None
    assert out.as_of == dt.date(2024, 5, 10)


@pytest.mark.django_db
def test_fmp_filer_excludes_future_and_stamps_filed_at() -> None:
    """The filer path picks the latest knowable period and stores its real
    filing date, not the query as_of."""
    from apps.data.models import InstitutionalHolding

    payload = [
        {
            "date": "2023-12-31",
            "investorName": "BERKSHIRE",
            "cusip": "037833100",
            "securityName": "Apple",
            "symbol": "AAPL",
            "marketValue": 1_000,
            "sharesNumber": 100,
        },
        {
            "date": "2024-06-30",  # period_end+45 = 2024-08-14 → future
            "investorName": "BERKSHIRE",
            "cusip": "594918104",
            "securityName": "Microsoft",
            "symbol": "MSFT",
            "marketValue": 999,
            "sharesNumber": 999,
        },
    ]
    fmp = FmpProvider(api_key="k", http=_FakeHttp([_Resp(json_data=payload)]))
    out = fmp.get_filer_portfolio("1067983", as_of=dt.date(2024, 4, 30))
    assert out is not None
    assert out.period_end == dt.date(2023, 12, 31)
    assert out.as_of == dt.date(2024, 2, 14)  # 2023-12-31 + 45d
    assert [h.ticker for h in out.holdings] == ["AAPL"]
    rows = list(InstitutionalHolding.objects.filter(source="fmp"))
    assert len(rows) == 1
    assert rows[0].filed_at == dt.date(2024, 2, 14)
    assert rows[0].ticker == "AAPL"


# ---------------------------------------------------------------------------
# Resolver degradation on a non-entitlement FMP error
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_resolver_falls_back_to_edgar_on_httpx_error() -> None:
    """A wrong slug (404) / transport error → EDGAR, not a hard failure."""
    from apps.data.models import IssuerOwnershipSnapshot

    IssuerOwnershipSnapshot.objects.create(
        ticker="AAPL",
        period_end=dt.date(2024, 3, 31),
        as_of_date=dt.date(2024, 5, 15),
        num_holders=7,
        total_shares=1000,
        total_value_usd=2_000_000,
        source="edgar",
    )
    fmp = FmpProvider(api_key="k", http=_FakeHttp([_Resp(status_code=404)]))
    resolver = OwnershipResolver(fmp=fmp, edgar=EdgarProvider(http=_FakeHttp([])))
    out = resolver.get_issuer_ownership("AAPL", as_of=dt.date(2024, 6, 30))
    assert out is not None
    assert out.source == "edgar"
    assert out.num_holders == 7


@pytest.mark.django_db
def test_resolver_filer_falls_back_to_edgar_on_httpx_error() -> None:
    """The filer path likewise degrades to EDGAR on an FMP transport error."""
    from apps.data.models import InstitutionalHolding

    cik = "1067983"
    InstitutionalHolding.objects.create(
        filer_cik=cik, filer_name="BRK", issuer_cusip="037833100",
        issuer_name="Apple", ticker="AAPL", period_end=dt.date(2023, 12, 31),
        filed_at=dt.date(2024, 2, 14), shares=100, value_usd=1000,
        put_call="", source="edgar",
    )
    fmp = FmpProvider(api_key="k", http=_FakeHttp([_Resp(status_code=404)]))
    resolver = OwnershipResolver(fmp=fmp, edgar=EdgarProvider(http=_FakeHttp([])))
    out = resolver.get_filer_portfolio(cik, as_of=dt.date(2024, 4, 30))
    assert out is not None
    assert out.source == "edgar"
    assert out.holdings[0].shares == 100


# ---------------------------------------------------------------------------
# EDGAR: period_end recovery + PRN skip
# ---------------------------------------------------------------------------


def _edgar_responses(*, period_of_report, info_rows, filed="2024-02-14"):
    accn = "0001067983-24-000001"
    subs = {
        "name": "BERKSHIRE HATHAWAY INC",
        "filings": {
            "recent": {
                "form": ["13F-HR"],
                "filingDate": [filed],
                "accessionNumber": [accn],
                "periodOfReport": [period_of_report],
            }
        },
    }
    index = {"directory": {"item": [{"name": "form13fInfoTable.xml"}]}}
    xml = _info_table_xml(info_rows)
    return _FakeHttp(
        [
            _Resp(json_data=subs),
            _Resp(json_data=index),
            _Resp(text_data=xml),
        ]
    )


@pytest.mark.django_db
def test_edgar_period_end_snaps_when_periodofreport_missing() -> None:
    """A blank periodOfReport snaps to the prior calendar quarter-end, not
    the filing date."""
    http = _edgar_responses(
        period_of_report="",
        info_rows=[
            {"name": "APPLE INC", "cusip": "037833100", "value": 1000, "shares": 100},
        ],
        filed="2024-02-14",
    )
    from apps.data.models import InstitutionalHolding

    edgar = EdgarProvider(http=http)
    out = edgar.get_filer_portfolio("1067983", as_of=dt.date(2024, 12, 31))
    assert out is not None
    assert out.period_end == dt.date(2023, 12, 31)  # NOT 2024-02-14
    # The persisted holding carries the snapped period_end (not filed_at).
    row = InstitutionalHolding.objects.get(source="edgar", issuer_cusip="037833100")
    assert row.period_end == dt.date(2023, 12, 31)
    assert row.filed_at == dt.date(2024, 2, 14)


@pytest.mark.django_db
def test_edgar_skips_prn_rows() -> None:
    """PRN (debt-principal) rows are excluded from the long-equity portfolio."""
    http = _edgar_responses(
        period_of_report="2023-12-31",
        info_rows=[
            {"name": "APPLE INC", "cusip": "037833100", "value": 1000,
             "shares": 100, "ssh_type": "SH"},
            {"name": "SOME BOND", "cusip": "111111111", "value": 999,
             "shares": 999, "ssh_type": "PRN"},
        ],
    )
    edgar = EdgarProvider(http=http)
    out = edgar.get_filer_portfolio("1067983", as_of=dt.date(2024, 12, 31))
    assert out is not None
    assert len(out.holdings) == 1
    assert out.holdings[0].issuer_cusip == "037833100"
    assert all(h.shares != 999 for h in out.holdings)


# ---------------------------------------------------------------------------
# EDGAR cached read excludes a same-period amendment filed after as_of
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_edgar_cached_excludes_future_same_period_amendment() -> None:
    """An amendment for the same period_end filed after as_of must not leak
    through the cached-first by-filer read."""
    from apps.data.models import InstitutionalHolding

    cik = "1067983"
    period = dt.date(2023, 12, 31)
    # Original 13F-HR: knowable.
    InstitutionalHolding.objects.create(
        filer_cik=cik, filer_name="BRK", issuer_cusip="037833100",
        issuer_name="Apple", ticker="AAPL", period_end=period,
        filed_at=dt.date(2024, 2, 14), shares=100, value_usd=1000,
        put_call="", source="edgar",
    )
    # 13F-HR/A amendment, SAME period, filed AFTER as_of → future leak.
    InstitutionalHolding.objects.create(
        filer_cik=cik, filer_name="BRK", issuer_cusip="594918104",
        issuer_name="Microsoft", ticker="MSFT", period_end=period,
        filed_at=dt.date(2024, 5, 15), shares=999, value_usd=999,
        put_call="", source="edgar",
    )
    edgar = EdgarProvider(http=_FakeHttp([]))  # cached path: no network
    out = edgar.get_filer_portfolio(cik, as_of=dt.date(2024, 4, 30))
    assert out is not None
    cusips = {h.issuer_cusip for h in out.holdings}
    assert cusips == {"037833100"}  # the amendment's row is excluded
    assert all(h.shares != 999 for h in out.holdings)
