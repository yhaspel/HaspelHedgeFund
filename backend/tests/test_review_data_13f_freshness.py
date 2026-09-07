"""Adversarial review (reviewer: data) — 13F data layer + adjusted-close guard.

Proof tests for:
  * F-CUSIP-NO-PRODUCER / F-13F-QUARTER: the whole SEC EDGAR bulk 13F path was
    dead end-to-end — nothing ever wrote a ``CusipTicker`` row so every
    ingested holding landed with ``ticker=""`` and ``_aggregate`` built ZERO
    ``IssuerOwnershipSnapshot`` rows, ``get_filer_portfolio`` had no callers,
    there was no 13F UI, and SEC renamed the data sets in 2024 so the
    quarterly beat 404'd on every run. DELETED in WAVE-3 P2 (command, task
    body, models, migration) rather than fixed; what survives is covered by
    ``test_wave3_p2_13f_removal.py``. The proof tests that only exercised the
    deleted code are gone with it.
  * F-ADJ-TAIL-ERASE: ``normalize_adjusted_tail`` flattens ``adjusted_close``
    back to ``close`` for every bar on/after the last KNOWN dividend; since
    dividends are only ever loaded by a one-off management command, each
    real dividend paid after that run is erased from the total-return series
    on the next pod cycle.
  * F-EDGAR-FROZEN: the *filings* cache (a different, live provider) never
    refreshed once ``limit`` rows existed.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.data.models import CorporateAction, DailyBar

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# F-ADJ-TAIL-ERASE
# ---------------------------------------------------------------------------


def _seed_stale_dividend_table(t: str = "TLT") -> None:
    last_known_ex = dt.date(2026, 6, 1)  # last row the one-off backfill loaded
    CorporateAction.objects.create(
        ticker=t, as_of_date=last_known_ex, kind=CorporateAction.CASH_DIVIDEND,
        amount=Decimal("0.30"), source="fmp",
    )
    # Two REAL monthly dividends since then (Jul 1, Aug 1) that FMP's
    # dividend-adjusted series already reflects: bars before Aug 1 are
    # adjusted down by ~0.3% per later dividend.
    rows = [
        (dt.date(2026, 6, 15), "100", "99.40"),   # before Jul+Aug divs → 2 adjustments
        (dt.date(2026, 7, 15), "100", "99.70"),   # before Aug div → 1 adjustment
        (dt.date(2026, 8, 15), "100", "100.00"),  # after the last real div
    ]
    for d, close, adj in rows:
        DailyBar.objects.create(
            ticker=t, date=d, open=1, high=1, low=1, close=Decimal(close),
            adjusted_close=Decimal(adj), volume=1, source="fmp",
        )


def test_normalize_adjusted_tail_refreshes_dividends_before_normalising():
    """FIXED: given a provider, the dividend table is topped up first, so the
    real ex-date moves to Aug 1 and the Jun/Jul back-adjustments survive."""
    from apps.data.freshness import normalize_adjusted_tail

    _seed_stale_dividend_table()

    class _Prov:
        calls: list[str] = []

        def get_dividends(self, ticker, start, end):
            _Prov.calls.append(ticker)
            return [
                (dt.date(2026, 6, 1), Decimal("0.30")),
                (dt.date(2026, 7, 1), Decimal("0.30")),
                (dt.date(2026, 8, 1), Decimal("0.30")),
            ]

    fixed = normalize_adjusted_tail("TLT", provider=_Prov(), as_of=dt.date(2026, 9, 7))
    assert _Prov.calls == ["TLT"]
    assert fixed == 0
    adj = dict(DailyBar.objects.filter(ticker="TLT").values_list("date", "adjusted_close"))
    assert adj[dt.date(2026, 6, 15)] == Decimal("99.40")
    assert adj[dt.date(2026, 7, 15)] == Decimal("99.70")
    # ... and the newly-learned dividends are now on file.
    assert CorporateAction.objects.filter(
        ticker="TLT", kind=CorporateAction.CASH_DIVIDEND
    ).count() == 3


def test_normalize_adjusted_tail_refuses_a_stale_dividend_table_without_a_provider():
    """FIXED: with no provider and a dividend table >100d behind the newest
    bar, the normalisation is refused rather than erasing real adjustments."""
    from apps.data.freshness import normalize_adjusted_tail

    _seed_stale_dividend_table("VNQ")
    # Push the newest bar out so the gap to the Jun 1 ex-date exceeds 100 days.
    DailyBar.objects.create(
        ticker="VNQ", date=dt.date(2026, 9, 20), open=1, high=1, low=1,
        close=Decimal("100"), adjusted_close=Decimal("99.10"), volume=1, source="fmp",
    )
    assert normalize_adjusted_tail("VNQ") == 0
    adj = dict(DailyBar.objects.filter(ticker="VNQ").values_list("date", "adjusted_close"))
    assert adj[dt.date(2026, 6, 15)] == Decimal("99.40")
    assert adj[dt.date(2026, 7, 15)] == Decimal("99.70")


# ---------------------------------------------------------------------------
# F-EDGAR-FROZEN — filings cache never refreshes once `limit` rows exist
# ---------------------------------------------------------------------------


def test_edgar_recent_filings_refetch_when_the_newest_cached_row_is_old():
    """FIXED: a full cache is only trusted while its newest row is within
    ~100 days of ``as_of`` — otherwise EDGAR is consulted for newer filings."""
    from unittest.mock import MagicMock

    from apps.data.models import FilingRecord
    from apps.data.providers.edgar import EdgarProvider

    old = (("10-K", dt.date(2024, 2, 2)), ("10-Q", dt.date(2024, 5, 3)))
    for i, (form, filed) in enumerate(old):
        FilingRecord.objects.create(
            ticker="AAPL", form_type=form, filed_at=filed, period_end=filed,
            accession=f"0000320193-24-00000{i}", url="https://sec/x", text_excerpt="old",
        )
    http = MagicMock()
    http.get.side_effect = RuntimeError("EDGAR consulted")
    prov = EdgarProvider(user_agent="t t@t.com", http=http)
    with pytest.raises(RuntimeError, match="EDGAR consulted"):
        prov.get_recent_filings(
            "AAPL", as_of=dt.date(2026, 9, 7), form_types=["10-K", "10-Q"], limit=2
        )
    assert http.get.call_count == 1


def test_edgar_recent_filings_still_short_circuit_on_a_current_cache():
    """A full AND current cache must not hit the network — the whole point of
    the cache."""
    from unittest.mock import MagicMock

    from apps.data.models import FilingRecord
    from apps.data.providers.edgar import EdgarProvider

    as_of = dt.date(2026, 9, 7)
    recent = (
        ("10-K", as_of - dt.timedelta(days=90)),
        ("10-Q", as_of - dt.timedelta(days=20)),
    )
    for i, (form, filed) in enumerate(recent):
        FilingRecord.objects.create(
            ticker="AAPL", form_type=form, filed_at=filed, period_end=filed,
            accession=f"0000320193-26-00000{i}", url="https://sec/x", text_excerpt="new",
        )
    http = MagicMock()
    prov = EdgarProvider(user_agent="t t@t.com", http=http)
    out = prov.get_recent_filings(
        "AAPL", as_of=as_of, form_types=["10-K", "10-Q"], limit=2
    )
    assert len(out) == 2
    assert http.get.call_count == 0
