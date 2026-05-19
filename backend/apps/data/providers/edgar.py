"""SEC EDGAR adapter for company filings.

EDGAR is open (no key); SEC fair-access policy requires a real contact
in the User-Agent header — see https://www.sec.gov/os/accessing-edgar-data.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import re

import httpx
from django.conf import settings
from django.db import transaction

from ..interfaces import Filing
from ..models import FilingRecord

# Sections we index for 10-K / 10-Q. The keys are normalized; the values are
# regex fragments that match the section heading in the stripped text.
SECTION_PATTERNS = {
    "risk_factors": re.compile(r"item\s*1a[.\s]+risk\s+factors", re.IGNORECASE),
    "mdna": re.compile(
        r"item\s*7[.\s]+management.?s\s+discussion\s+and\s+analysis", re.IGNORECASE
    ),
    "business": re.compile(r"item\s*1[.\s]+business", re.IGNORECASE),
}
NEXT_ITEM_RE = re.compile(r"\bitem\s*\d+[ab]?[.\s]+", re.IGNORECASE)

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
            excerpt, full_path, section_index = self._fetch_and_index(
                url_doc, accession=accession, form_type=form
            )
            created.append(
                FilingRecord(
                    ticker=ticker,
                    form_type=form,
                    filed_at=filed_at,
                    period_end=period_end,
                    accession=accession,
                    url=url_doc,
                    text_excerpt=excerpt,
                    full_text_path=full_path,
                    section_index=section_index,
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

    def _fetch_and_index(
        self, url: str, *, accession: str, form_type: str
    ) -> tuple[str, str, dict]:
        """Download the filing, write full text to MEDIA_ROOT/filings/, build a
        section index (char offsets) for 10-K/10-Q items, return (excerpt,
        relative_path, section_index)."""
        try:
            resp = self._http.get(url)
            resp.raise_for_status()
        except httpx.HTTPError:
            return "", "", {}
        text = _strip_html(resp.text)
        rel_path = ""
        try:
            digest = hashlib.sha256(accession.encode()).hexdigest()[:2]
            sub = os.path.join("filings", digest)
            abs_dir = os.path.join(settings.MEDIA_ROOT, sub)
            os.makedirs(abs_dir, exist_ok=True)
            fname = f"{accession.replace('-', '')}.txt"
            abs_path = os.path.join(abs_dir, fname)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(text)
            rel_path = os.path.join(sub, fname)
        except OSError:
            rel_path = ""

        section_index: dict = {}
        if form_type in ("10-K", "10-Q"):
            for name, pat in SECTION_PATTERNS.items():
                section_index[name] = _locate_section(name, pat, text, url)
                # Drop entries that didn't locate; keeps the dict shape but
                # callers can detect missing sections via missing key.
                if section_index[name] is None:
                    del section_index[name]
        return text[:4000], rel_path, section_index


# Minimum body length for a real section. TOC entries are short (the heading
# line plus a page number / dotted leader), bodies are thousands of chars.
_MIN_SECTION_BODY = 1500


def _locate_section(
    name: str, pat: "re.Pattern[str]", text: str, source_url: str
) -> dict | None:
    """Pick the real section body, not the Table of Contents entry.

    10-Ks repeat every "Item 1A. Risk Factors" heading once in the TOC
    (followed by a page number, very short span to next item) and once at
    the actual section body (followed by thousands of characters before
    the next item). We pick the candidate whose span to the next item is
    the largest, breaking ties by latest position.

    Returns a dict with start/end and diagnostics (`source_url`,
    `matched_heading`, `char_count`) or None if no plausible body was
    found.
    """
    candidates: list[tuple[int, int, str]] = []
    for m in pat.finditer(text):
        start = m.start()
        tail = text[m.end():]
        nxt = NEXT_ITEM_RE.search(tail)
        end = m.end() + (nxt.start() if nxt else min(len(tail), 80_000))
        candidates.append((start, end, m.group(0)))

    # Keep only candidates with a body long enough to be the real section.
    bodies = [c for c in candidates if (c[1] - c[0]) >= _MIN_SECTION_BODY]
    chosen_pool = bodies or candidates
    if not chosen_pool:
        return None
    # Largest span wins; ties broken by latest position (TOC entries are early).
    start, end, heading = max(chosen_pool, key=lambda c: (c[1] - c[0], c[0]))
    return {
        "start": start,
        "end": end,
        "source_url": source_url,
        "matched_heading": heading,
        "char_count": end - start,
    }

    @staticmethod
    def load_section(record: FilingRecord, section: str) -> str:
        info = (record.section_index or {}).get(section)
        if not info or not record.full_text_path:
            return ""
        path = os.path.join(settings.MEDIA_ROOT, record.full_text_path)
        try:
            with open(path, encoding="utf-8") as f:
                full = f.read()
        except OSError:
            return ""
        return full[info["start"]: info["end"]]


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
