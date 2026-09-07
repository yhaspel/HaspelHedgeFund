"""Post-review hardening tests for the 13F ownership layer (P4 prereq).

Covers the fixes applied after the adversarial review:
  - The FMP by-issuer path is point-in-time gated (no look-ahead on an entitled
    Ultimate key) and stamps the *real* filing date, not `as_of`.
  - The resolver degrades to ``None`` on any FMP transport error (e.g. a wrong
    endpoint slug → 404), not just OwnershipNotEntitled and never a hard fail.

WAVE-3 P2 deleted the SEC EDGAR bulk 13F path (by-filer view, CUSIP map,
data-set ingest, aggregation) — the tests here that only covered that code went
with it.

No live network: FMP is a tiny fake httpx client.
"""

from __future__ import annotations

import datetime as dt

import httpx
import pytest

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


# ---------------------------------------------------------------------------
# Resolver degradation on a non-entitlement FMP error
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_resolver_returns_none_on_httpx_error() -> None:
    """A wrong slug (404) / transport error degrades to ``None``, not a hard
    failure. WAVE-3 P2: there is no EDGAR bulk fallback to degrade *to* any
    more — the caller reports "requires FMP Ultimate" instead."""
    fmp = FmpProvider(api_key="k", http=_FakeHttp([_Resp(status_code=404)]))
    resolver = OwnershipResolver(fmp=fmp)
    assert resolver.get_issuer_ownership("AAPL", as_of=dt.date(2024, 6, 30)) is None


@pytest.mark.django_db
def test_resolver_records_a_transport_outage_then_returns_none(monkeypatch) -> None:
    """A genuine connect/read outage is counted toward the operator alert."""
    seen: list[str] = []

    class _Boom:
        def get_issuer_ownership(self, ticker, *, as_of):
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(
        "apps.data.providers.ownership._record_provider_outage",
        lambda provider, exc: seen.append(provider),
    )
    resolver = OwnershipResolver(fmp=_Boom())
    assert resolver.get_issuer_ownership("AAPL", as_of=dt.date(2024, 6, 30)) is None
    assert seen == ["fmp-ownership"]
