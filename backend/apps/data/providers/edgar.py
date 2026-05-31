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

from ..interfaces import FilerHolding, FilerPortfolio, Filing
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

    # ---- 13F by-filer (P4 ownership) -------------------------------------

    def _parse_info_table(self, xml_text: str) -> list[dict]:
        """Parse a 13F INFORMATION TABLE XML into raw dict rows.

        Handles the (optional) default namespace by matching on local-name.
        """
        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_text)

        def local(tag: str) -> str:
            return tag.rsplit("}", 1)[-1]

        rows: list[dict] = []
        for el in root:
            if local(el.tag) != "infoTable":
                continue
            row: dict = {
                "name": "",
                "cusip": "",
                "value": 0,
                "shares": 0,
                "put_call": "",
            }
            ssh_type = ""
            for child in el.iter():
                lname = local(child.tag)
                if lname == "nameOfIssuer":
                    row["name"] = (child.text or "").strip()
                elif lname == "cusip":
                    row["cusip"] = (child.text or "").strip()
                elif lname == "value":
                    row["value"] = int(float((child.text or "0").strip() or 0))
                elif lname == "sshPrnamt":
                    row["shares"] = int(float((child.text or "0").strip() or 0))
                elif lname == "sshPrnamtType":
                    ssh_type = (child.text or "").strip()
                elif lname == "putCall":
                    row["put_call"] = (child.text or "").strip()
            # 13F covers long US equity only. Skip PRN rows (principal amount of
            # debt) so bond principal never inflates the share/value aggregate.
            if ssh_type.upper() == "PRN":
                continue
            rows.append(row)
        return rows

    def _cached_filer_portfolio(self, cik, as_of):
        """Serve a previously-persisted by-filer portfolio (PIT-safe)."""
        from ..models import InstitutionalHolding

        latest = (
            InstitutionalHolding.objects.filter(
                filer_cik=cik, source="edgar", filed_at__lte=as_of
            )
            .order_by("-period_end")
            .values_list("period_end", flat=True)
            .first()
        )
        if latest is None:
            return None
        rows = list(
            InstitutionalHolding.objects.filter(
                filer_cik=cik, source="edgar", period_end=latest,
                filed_at__lte=as_of,
            )
        )
        if not rows:
            return None
        return _filer_portfolio_from_rows(cik, rows[0].filer_name, latest, rows)

    def get_filer_portfolio(self, filer_cik, *, as_of) -> FilerPortfolio | None:
        """Newest 13F-HR(/A) with filingDate <= as_of; cached-first.

        Fetches submissions JSON, locates the newest 13F-HR(/A), parses the
        INFORMATION TABLE XML, normalizes value units (x1000 pre-2023-01-03),
        persists InstitutionalHolding(source="edgar"), returns a FilerPortfolio.
        """
        from ..models import InstitutionalHolding
        from .ownership import cusip_to_ticker

        cik = str(filer_cik).lstrip("0")
        cik10 = cik.zfill(10)
        cached = self._cached_filer_portfolio(cik, as_of)
        if cached is not None:
            return cached

        subs = self._http.get(
            f"{DATA_BASE}/submissions/CIK{cik10}.json"
        ).json()
        recent = subs.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accns = recent.get("accessionNumber", [])
        periods = recent.get("periodOfReport", [])
        as_of_iso = as_of.isoformat()
        idx = None
        for i, form in enumerate(forms):
            if form in ("13F-HR", "13F-HR/A") and dates[i] <= as_of_iso:
                idx = i
                break
        if idx is None:
            return None
        accn = accns[idx].replace("-", "")
        filed = dt.date.fromisoformat(dates[idx])
        period_end = None
        if idx < len(periods) and periods[idx]:
            try:
                period_end = dt.date.fromisoformat(periods[idx])
            except ValueError:
                period_end = None
        # A 13F is filed up to ~45 days after quarter-end. If periodOfReport is
        # missing/unparseable, snap to the most recent calendar quarter-end on
        # or before the filing date rather than using filed_at verbatim (which
        # would corrupt the period_end key and the cached-first lookup).
        if period_end is None:
            period_end = _quarter_end_on_or_before(filed)
        filer_name = subs.get("name", "")
        base = f"{ARCHIVES_BASE}/{cik}/{accn}"
        index = self._http.get(f"{base}/index.json").json()
        items = index.get("directory", {}).get("item", [])
        xml_name = None
        for item in items:
            low = item.get("name", "").lower()
            if low.endswith(".xml") and (
                "info" in low or "table" in low or "form13f" in low
            ):
                xml_name = item["name"]
                break
        if xml_name is None:
            for item in items:
                low = item.get("name", "").lower()
                if low.endswith(".xml") and "primary_doc" not in low:
                    xml_name = item["name"]
                    break
        if xml_name is None:
            return None
        xml_text = self._http.get(f"{base}/{xml_name}").text
        raw = self._parse_info_table(xml_text)
        mult = 1000 if filed < dt.date(2023, 1, 3) else 1
        objs = []
        for r in raw:
            objs.append(
                InstitutionalHolding(
                    filer_cik=cik,
                    filer_name=filer_name,
                    issuer_cusip=r["cusip"],
                    issuer_name=r["name"],
                    ticker=cusip_to_ticker(r["cusip"]) or "",
                    period_end=period_end,
                    filed_at=filed,
                    shares=int(r["shares"]),
                    value_usd=int(r["value"]) * mult,
                    put_call=r["put_call"],
                    source="edgar",
                )
            )
        if objs:
            InstitutionalHolding.objects.bulk_create(
                objs, ignore_conflicts=True
            )
        return _filer_portfolio_from_rows(cik, filer_name, period_end, objs)


# Minimum body length for a real section. TOC entries are short (the heading
# line plus a page number / dotted leader), bodies are thousands of chars.
_MIN_SECTION_BODY = 1500


def _locate_section(
    name: str, pat: re.Pattern[str], text: str, source_url: str
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

def _load_section(record: FilingRecord, section: str) -> str:
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


EdgarProvider.load_section = staticmethod(_load_section)


def _quarter_end_on_or_before(d: dt.date) -> dt.date:
    """Most recent calendar quarter-end (Mar 31 / Jun 30 / Sep 30 / Dec 31) on
    or before ``d``. Used to recover a 13F report period when the filing's
    ``periodOfReport`` is absent."""
    for month, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
        qe = dt.date(d.year, month, day)
        if qe <= d:
            return qe
    return dt.date(d.year - 1, 12, 31)


def _filer_portfolio_from_rows(cik, filer_name, period_end, rows):
    """Build a FilerPortfolio dataclass from InstitutionalHolding rows."""
    total = sum(int(r.value_usd) for r in rows) or 0
    holdings = [
        FilerHolding(
            issuer_cusip=r.issuer_cusip,
            issuer_name=r.issuer_name,
            ticker=r.ticker,
            shares=int(r.shares),
            value_usd=int(r.value_usd),
            put_call=r.put_call,
            weight_pct=(
                round(int(r.value_usd) / total * 100, 4) if total else None
            ),
        )
        for r in rows
    ]
    filed = getattr(rows[0], "filed_at", None) or period_end if rows else period_end
    return FilerPortfolio(
        filer_cik=cik,
        filer_name=filer_name,
        period_end=period_end,
        as_of=filed,
        total_value_usd=total,
        holdings=holdings,
        source="edgar",
    )


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
