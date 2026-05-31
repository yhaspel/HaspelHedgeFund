"""Ingest a quarter's SEC Form 13F structured data set.

Downloads the quarterly ZIP (or reads a local one via ``--url file://...``),
parses SUBMISSION.tsv + COVERPAGE.tsv + INFOTABLE.tsv, normalizes value
units, resolves CUSIP->ticker, persists InstitutionalHolding(source=edgar),
then aggregates per (ticker, period_end) into IssuerOwnershipSnapshot with
QoQ deltas and new/closed-position sets versus the prior quarter.

Idempotent: bulk_create(ignore_conflicts=True) + update_or_create snapshots.
SEC 13F data sets are public domain.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import zipfile
from collections import defaultdict
from urllib.request import urlopen

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand

DATASET_URL_TEMPLATE = (
    "https://www.sec.gov/files/structureddata/data/"
    "form-13f-data-sets/{quarter}_form13f.zip"
)

# SEC switched <value> from $1000s to whole dollars on 2023-01-03.
_VALUE_BOUNDARY = dt.date(2023, 1, 3)


def _norm_value(value: int, filing_date) -> int:
    if filing_date is not None and filing_date < _VALUE_BOUNDARY:
        return value * 1000
    return value


def _parse_date(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _reader(zf: zipfile.ZipFile, name: str):
    """Yield dict rows from a tab-separated member (case-insensitive name)."""
    member = None
    for n in zf.namelist():
        if n.upper().endswith(name.upper()):
            member = n
            break
    if member is None:
        return
    with zf.open(member) as fh:
        text = io.TextIOWrapper(fh, encoding="utf-8", errors="replace")
        for row in csv.DictReader(text, delimiter="\t"):
            yield {(k or "").strip().upper(): v for k, v in row.items()}


class Command(BaseCommand):
    help = "Ingest a quarter's SEC Form 13F structured data set."

    def add_arguments(self, parser):
        parser.add_argument("--quarter", required=True, help="e.g. 2024Q4")
        parser.add_argument("--url", help="override the dataset ZIP URL")
        parser.add_argument(
            "--limit", type=int, default=0, help="cap INFOTABLE rows (debug)"
        )

    def handle(self, *args, **opts):
        quarter = opts["quarter"]
        url = opts.get("url") or DATASET_URL_TEMPLATE.format(
            quarter=quarter.lower()
        )
        content = self._download(url)
        zf = zipfile.ZipFile(io.BytesIO(content))

        # SUBMISSION.tsv -> {accession: (filing_date, period_end, cik)}
        subs = {}
        for row in _reader(zf, "SUBMISSION.tsv"):
            accn = (row.get("ACCESSION_NUMBER") or "").strip()
            if not accn:
                continue
            subs[accn] = (
                _parse_date(row.get("FILING_DATE", "")),
                _parse_date(row.get("PERIODOFREPORT", "")),
                (row.get("CIK") or "").strip().lstrip("0"),
            )

        # COVERPAGE.tsv -> {accession: filer_name}
        names = {}
        for row in _reader(zf, "COVERPAGE.tsv"):
            accn = (row.get("ACCESSION_NUMBER") or "").strip()
            if accn:
                names[accn] = (row.get("FILINGMANAGER_NAME") or "").strip()

        # INFOTABLE.tsv -> InstitutionalHolding rows
        from apps.data.models import InstitutionalHolding
        from apps.data.providers.ownership import cusip_to_ticker

        objs = []
        limit = opts.get("limit") or 0
        count = 0
        for row in _reader(zf, "INFOTABLE.tsv"):
            accn = (row.get("ACCESSION_NUMBER") or "").strip()
            sub = subs.get(accn)
            if sub is None:
                continue
            filing_date, period_end, cik = sub
            if period_end is None:
                continue
            # 13F covers long US equity only: skip PRN rows (debt principal) so
            # bond principal never inflates the share/value aggregate.
            if (row.get("SSHPRNAMTTYPE") or "").strip().upper() == "PRN":
                continue
            cusip = (row.get("CUSIP") or "").strip()
            value = int(float((row.get("VALUE") or "0").strip() or 0))
            shares = int(float((row.get("SSHPRNAMT") or "0").strip() or 0))
            objs.append(
                InstitutionalHolding(
                    filer_cik=cik,
                    filer_name=names.get(accn, ""),
                    issuer_cusip=cusip,
                    issuer_name=(row.get("NAMEOFISSUER") or "").strip(),
                    ticker=cusip_to_ticker(cusip) or "",
                    period_end=period_end,
                    filed_at=filing_date,
                    shares=shares,
                    value_usd=_norm_value(value, filing_date),
                    put_call=(row.get("PUTCALL") or "").strip(),
                    source="edgar",
                )
            )
            count += 1
            if len(objs) >= 5000:
                InstitutionalHolding.objects.bulk_create(
                    objs, ignore_conflicts=True
                )
                objs = []
            if limit and count >= limit:
                break
        if objs:
            InstitutionalHolding.objects.bulk_create(objs, ignore_conflicts=True)

        self.stdout.write(
            self.style.SUCCESS(f"Ingested {count} 13F holdings for {quarter}")
        )
        n_snaps = self._aggregate()
        self.stdout.write(
            self.style.SUCCESS(f"Built {n_snaps} issuer snapshots")
        )

    def _download(self, url: str) -> bytes:
        if url.startswith("file://"):
            with urlopen(url) as fh:  # noqa: S310 (local fixture only)
                return fh.read()
        resp = httpx.get(
            url,
            headers={"User-Agent": settings.EDGAR_USER_AGENT},
            timeout=120.0,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return resp.content

    def _aggregate(self) -> int:
        """Aggregate persisted holdings into IssuerOwnershipSnapshot rows."""
        from apps.data.models import (
            InstitutionalHolding,
            IssuerOwnershipSnapshot,
        )

        groups = defaultdict(list)
        qs = InstitutionalHolding.objects.filter(
            source="edgar", put_call=""
        ).exclude(ticker="")
        for h in qs.iterator():
            groups[(h.ticker, h.period_end)].append(h)

        by_ticker = defaultdict(list)
        for ticker, period in groups:
            by_ticker[ticker].append(period)
        for ticker in by_ticker:
            by_ticker[ticker].sort()

        n = 0
        for (ticker, period), rows in groups.items():
            filers = {r.filer_cik for r in rows}
            total_value = sum(int(r.value_usd) for r in rows)
            total_shares = sum(int(r.shares) for r in rows)
            top = sorted(rows, key=lambda r: int(r.value_usd), reverse=True)[:10]
            top_holders = [
                {
                    "filer_cik": r.filer_cik,
                    "filer_name": r.filer_name,
                    "shares": int(r.shares),
                    "value_usd": int(r.value_usd),
                }
                for r in top
            ]
            filed_dates = [r.filed_at for r in rows if r.filed_at]
            as_of = max(filed_dates) if filed_dates else period

            periods = by_ticker[ticker]
            prior_period = None
            for p in reversed(periods):
                if p < period:
                    prior_period = p
                    break
            qoq_pct = None
            new_positions = []
            closed_positions = []
            if prior_period is not None:
                prior_rows = groups[(ticker, prior_period)]
                prior_value = sum(int(r.value_usd) for r in prior_rows)
                if prior_value:
                    qoq_pct = (total_value - prior_value) / prior_value * 100
                prior_filers = {r.filer_cik for r in prior_rows}
                new_positions = sorted(filers - prior_filers)
                closed_positions = sorted(prior_filers - filers)

            IssuerOwnershipSnapshot.objects.update_or_create(
                ticker=ticker,
                period_end=period,
                source="edgar",
                defaults={
                    "issuer_cusip": rows[0].issuer_cusip,
                    "as_of_date": as_of,
                    "num_holders": len(filers),
                    "total_shares": total_shares,
                    "total_value_usd": total_value,
                    "qoq_value_change_pct": qoq_pct,
                    "top_holders": top_holders,
                    "new_positions": new_positions,
                    "closed_positions": closed_positions,
                },
            )
            n += 1
        return n
