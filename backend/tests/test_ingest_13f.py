"""Tests for the ``ingest_13f_datasets`` management command.

A trimmed SEC Form 13F structured data set (SUBMISSION / COVERPAGE /
INFOTABLE TSVs) is packaged into a tiny ZIP fixture and handed to the
command via its ``--url file://...`` escape hatch, so the whole flow runs
offline with no SEC network calls. We assert that:

* INFOTABLE rows land as ``InstitutionalHolding`` rows with the right
  CUSIP->ticker mapping, and that an aggregated ``IssuerOwnershipSnapshot``
  is rebuilt from them (excluding put/call options);
* re-running the ingest is idempotent (``bulk_create(ignore_conflicts=True)``
  plus ``update_or_create`` keep counts and the snapshot stable);
* a CUSIP with no ``CusipTicker`` mapping is stored with ``ticker=""``
  rather than raising.

The ZIP is built in a fixture so the bytes stay tiny and the column
layout matches exactly what the parser reads.
"""
from __future__ import annotations

import datetime as dt
import io
import zipfile

import pytest
from django.core.management import call_command

from apps.data.models import (
    CusipTicker,
    InstitutionalHolding,
    IssuerOwnershipSnapshot,
)

# CUSIP for Apple Inc. — mapped to a ticker in the test setup.
_AAPL_CUSIP = "037833100"
# CUSIP with no CusipTicker row — must resolve to ticker="" without error.
_UNKNOWN_CUSIP = "999999999"

_SUBMISSION_TSV = (
    "ACCESSION_NUMBER\tFILING_DATE\tPERIODOFREPORT\tCIK\n"
    "0000000001-24-000001\t14-Feb-2024\t31-Dec-2023\t1067983\n"
    "0000000002-24-000002\t14-Feb-2024\t31-Dec-2023\t1649339\n"
)
_COVERPAGE_TSV = (
    "ACCESSION_NUMBER\tFILINGMANAGER_NAME\n"
    "0000000001-24-000001\tBERKSHIRE HATHAWAY INC\n"
    "0000000002-24-000002\tSCION ASSET MANAGEMENT LLC\n"
)
# Berkshire: a long AAPL stake + an AAPL Put (the Put must be excluded from
# the aggregated snapshot). Scion: a long AAPL stake + a stake in an issuer
# whose CUSIP has no mapping.
_INFOTABLE_TSV = (
    "ACCESSION_NUMBER\tNAMEOFISSUER\tCUSIP\tVALUE\tSSHPRNAMT\tPUTCALL\n"
    f"0000000001-24-000001\tAPPLE INC\t{_AAPL_CUSIP}\t1000000\t10000\t\n"
    f"0000000001-24-000001\tAPPLE INC\t{_AAPL_CUSIP}\t50000\t500\tPut\n"
    f"0000000002-24-000002\tAPPLE INC\t{_AAPL_CUSIP}\t400000\t4000\t\n"
    f"0000000002-24-000002\tMYSTERY CORP\t{_UNKNOWN_CUSIP}\t250000\t2500\t\n"
)


@pytest.fixture
def dataset_url(tmp_path) -> str:
    """Write a trimmed 13F dataset ZIP to disk and return a ``file://`` URL.

    Built in-process so the fixture bytes stay tiny and the TSV column
    layout matches exactly what the command's parser reads.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SUBMISSION.tsv", _SUBMISSION_TSV)
        zf.writestr("COVERPAGE.tsv", _COVERPAGE_TSV)
        zf.writestr("INFOTABLE.tsv", _INFOTABLE_TSV)
    path = tmp_path / "2023q4_form13f.zip"
    path.write_bytes(buf.getvalue())
    return path.as_uri()


@pytest.mark.django_db
def test_ingest_persists_holdings_and_snapshot(dataset_url: str) -> None:
    # Map the AAPL CUSIP; leave _UNKNOWN_CUSIP unmapped on purpose.
    CusipTicker.objects.create(
        cusip=_AAPL_CUSIP, ticker="AAPL", issuer_name="APPLE INC"
    )

    call_command("ingest_13f_datasets", quarter="2023Q4", url=dataset_url)

    # All four INFOTABLE rows are persisted (incl. the Put and the unmapped).
    holdings = InstitutionalHolding.objects.all()
    assert holdings.count() == 4

    # CUSIP mapping resolved for AAPL; the long AAPL rows carry the ticker.
    aapl_longs = holdings.filter(
        issuer_cusip=_AAPL_CUSIP, put_call=""
    )
    assert aapl_longs.count() == 2
    assert all(h.ticker == "AAPL" for h in aapl_longs)

    # Values stored as whole dollars: filed 2024 is past the 2023-01-03
    # boundary, so no x1000 normalization is applied.
    berkshire = holdings.get(filer_cik="1067983", put_call="")
    assert berkshire.value_usd == 1_000_000
    assert berkshire.shares == 10_000
    assert berkshire.filer_name == "BERKSHIRE HATHAWAY INC"
    assert berkshire.period_end == dt.date(2023, 12, 31)
    assert berkshire.filed_at == dt.date(2024, 2, 14)

    # The aggregated snapshot covers AAPL only (the unmapped CUSIP and the
    # Put are excluded from aggregation).
    snaps = IssuerOwnershipSnapshot.objects.all()
    assert snaps.count() == 1
    snap = snaps.get(ticker="AAPL")
    assert snap.period_end == dt.date(2023, 12, 31)
    assert snap.source == "edgar"
    # Two distinct filers hold long AAPL; the Put is not counted.
    assert snap.num_holders == 2
    # 1,000,000 (Berkshire long) + 400,000 (Scion long); Put excluded.
    assert snap.total_value_usd == 1_400_000
    assert snap.total_shares == 14_000
    # First-ever quarter for this ticker -> no QoQ baseline.
    assert snap.qoq_value_change_pct is None
    # top_holders is a JSON list of dicts ordered by value desc.
    assert [h["filer_cik"] for h in snap.top_holders] == ["1067983", "1649339"]


@pytest.mark.django_db
def test_unresolvable_cusip_yields_blank_ticker(dataset_url: str) -> None:
    # Map AAPL only; _UNKNOWN_CUSIP intentionally has no CusipTicker row.
    CusipTicker.objects.create(
        cusip=_AAPL_CUSIP, ticker="AAPL", issuer_name="APPLE INC"
    )

    call_command("ingest_13f_datasets", quarter="2023Q4", url=dataset_url)

    mystery = InstitutionalHolding.objects.get(issuer_cusip=_UNKNOWN_CUSIP)
    assert mystery.ticker == ""
    assert mystery.issuer_name == "MYSTERY CORP"
    # An unmapped issuer never produces a snapshot.
    assert not IssuerOwnershipSnapshot.objects.filter(
        issuer_cusip=_UNKNOWN_CUSIP
    ).exists()


@pytest.mark.django_db
def test_ingest_is_idempotent(dataset_url: str) -> None:
    CusipTicker.objects.create(
        cusip=_AAPL_CUSIP, ticker="AAPL", issuer_name="APPLE INC"
    )

    call_command("ingest_13f_datasets", quarter="2023Q4", url=dataset_url)
    holdings_after_first = InstitutionalHolding.objects.count()
    snap_first = IssuerOwnershipSnapshot.objects.get(ticker="AAPL")

    # Re-run: ignore_conflicts on holdings + update_or_create on snapshots
    # must not duplicate rows nor perturb the aggregated values.
    call_command("ingest_13f_datasets", quarter="2023Q4", url=dataset_url)

    assert InstitutionalHolding.objects.count() == holdings_after_first
    assert IssuerOwnershipSnapshot.objects.count() == 1
    snap_second = IssuerOwnershipSnapshot.objects.get(ticker="AAPL")
    assert snap_second.pk == snap_first.pk
    assert snap_second.total_value_usd == snap_first.total_value_usd
    assert snap_second.total_shares == snap_first.total_shares
    assert snap_second.num_holders == snap_first.num_holders
