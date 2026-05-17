"""SEC EDGAR adapter for company filings.

EDGAR is open (no key); SEC fair-access policy requires a real contact
in the User-Agent header — see https://www.sec.gov/os/accessing-edgar-data.
"""
from __future__ import annotations

import datetime as dt
import re

import httpx
from django.conf import settings
from django.db import transaction

from ..interfaces import Filing
from ..models import FilingRecord

DATA_BASE = "https://data.sec.gov"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
SOURCE = "edgar"


class EdgarProvider:
    name = SOURCE

    def __init__(self, user_agent: str | None = None, http: httpx.Client | None = None) -> None:
        self.user_agent = user_agent or settings.EDGAR_USER_AGENT
        self._http = http or httpx.Client(
            timeout=30.0, headers={"User-Agent": self.user_agent}
        )

    def get_recent_filings(
        self,
        ticker: str,
        *,
        as_of: dt.date,
        form_types: list[str],
        limit: int = 4,
    ) -> list[Filing]:
        cached = list(
            FilingRecord.objects.filter(
                ticker=ticker, form_type__in=form_types, filed_at__lte=as_of
            ).order_by("-filed_at")[:limit]
        )
        if len(cached) >= limit:
            return [_to_dataclass(r) for r in cached]

        cik = self._lookup_cik(ticker)
        url = f"{DATA_BASE}/submissions/CIK{cik:010d}.json"
        resp = self._http.get(url)
        resp.raise_for_status()
        payload = resp.json()
        recent = payload.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        filed_dates = recent.get("filingDate", [])
        period_ends = recent.get("reportDate", [])
        primary_docs = recent.get("primaryDocument", [])

        created: list[FilingRecord] = []
        for form, accession, filed_s, period_s, doc in zip(
            forms, accessions, filed_dates, period_ends, primary_docs, strict=False
        ):
            if form not in form_types:
                continue
            filed_at = dt.date.fromisoformat(filed_s)
            if filed_at > as_of:
                continue
            period_end = (
                dt.date.fromisoformat(period_s) if period_s else filed_at
            )
            acc_nodash = accession.replace("-", "")
            url_doc = f"{ARCHIVES_BASE}/{cik}/{acc_nodash}/{doc}"
            excerpt = self._fetch_excerpt(url_doc)
            created.append(
                FilingRecord(
                    ticker=ticker,
                    form_type=form,
                    filed_at=filed_at,
                    period_end=period_end,
                    accession=accession,
                    url=url_doc,
                    text_excerpt=excerpt,
                )
            )
            if len(created) >= limit:
                break
        with transaction.atomic():
            FilingRecord.objects.bulk_create(created, ignore_conflicts=True)
        rows = FilingRecord.objects.filter(
            ticker=ticker, form_type__in=form_types, filed_at__lte=as_of
        ).order_by("-filed_at")[:limit]
        return [_to_dataclass(r) for r in rows]

    def _lookup_cik(self, ticker: str) -> int:
        url = "https://www.sec.gov/files/company_tickers.json"
        resp = self._http.get(url)
        resp.raise_for_status()
        for row in resp.json().values():
            if row.get("ticker", "").upper() == ticker.upper():
                return int(row["cik_str"])
        raise LookupError(f"Ticker {ticker} not found in EDGAR")

    def _fetch_excerpt(self, url: str, max_chars: int = 4000) -> str:
        try:
            resp = self._http.get(url)
            resp.raise_for_status()
        except httpx.HTTPError:
            return ""
        text = _strip_html(resp.text)
        return text[:max_chars]


_TAG_RE = re.compile(r"<[^>]+>")
_WHITE_RE = re.compile(r"\s+")


def _strip_html(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    return _WHITE_RE.sub(" ", text).strip()


def _to_dataclass(r: FilingRecord) -> Filing:
    return Filing(
        ticker=r.ticker,
        form_type=r.form_type,
        filed_at=r.filed_at,
        period_end=r.period_end,
        accession=r.accession,
        url=r.url,
        text_excerpt=r.text_excerpt,
    )
