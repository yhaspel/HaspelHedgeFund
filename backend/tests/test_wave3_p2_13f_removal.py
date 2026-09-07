"""WAVE-3 P2 item 2 — the dead SEC EDGAR bulk 13F path is gone.

The path was dead end-to-end: nothing ever wrote a ``CusipTicker`` row so every
ingested holding landed with ``ticker=""`` and the aggregation (which excludes
blank tickers) built zero snapshots; ``get_filer_portfolio`` had no callers and
no UI; and SEC renamed the Form 13F data sets in 2024 so the quarterly beat
404'd on every run since.

This file is the guard that it stays gone, that the *filings* provider (a
different, live EDGAR surface) is untouched, and that the surviving
FMP-Ultimate lookup explains itself when it returns nothing.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import re

import pytest

pytestmark = pytest.mark.django_db

_BACKEND = pathlib.Path(__file__).resolve().parents[1]


def test_the_deleted_13f_surface_is_really_gone():
    import apps.data.interfaces as interfaces
    import apps.data.models as models
    import apps.data.providers.ownership as ownership
    from apps.data.providers.edgar import EdgarProvider
    from apps.data.providers.fmp import FmpProvider

    for name in ("CusipTicker", "IssuerOwnershipSnapshot", "InstitutionalHolding"):
        assert not hasattr(models, name), name
    for name in ("FilerPortfolio", "FilerHolding"):
        assert not hasattr(interfaces, name), name
    for name in ("resolve_cusip", "cusip_to_ticker", "cache_cusip"):
        assert not hasattr(ownership, name), name
    assert not hasattr(ownership.OwnershipResolver, "get_filer_portfolio")
    assert not hasattr(EdgarProvider, "get_filer_portfolio")
    assert not hasattr(EdgarProvider, "_parse_info_table")
    assert not hasattr(FmpProvider, "get_filer_portfolio")

    # The ingest command is gone too.
    from django.core.management import call_command
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call_command("ingest_13f_datasets")


def test_no_source_file_references_the_deleted_models():
    hits: list[str] = []
    # Code references only — the deletion is documented in prose (``…``) in
    # several docstrings, which is the point.
    doc_span = re.compile(r"``[^`]*``|\"\"\"|#.*$", re.MULTILINE)
    pattern = re.compile(
        r"\b(?:CusipTicker|IssuerOwnershipSnapshot|InstitutionalHolding)\s*[.(]"
        r"|\b(?:cache_cusip|cusip_to_ticker|resolve_cusip|get_filer_portfolio)\s*\("
        r"|import\s+[^\n]*\b(?:CusipTicker|IssuerOwnershipSnapshot"
        r"|InstitutionalHolding|FilerPortfolio|FilerHolding)\b"
    )
    for root in ("apps", "hedgefund_agents"):
        for py in (_BACKEND / root).rglob("*.py"):
            if "migrations" in py.parts:
                continue
            text = doc_span.sub(" ", py.read_text(encoding="utf-8", errors="replace"))
            if pattern.search(text):
                hits.append(str(py.relative_to(_BACKEND)))
    assert hits == [], f"dead 13F references remain: {hits}"


def test_the_beat_task_is_a_registered_no_op():
    """The beat schedule entry still points here until the coordinator removes
    it, so the task must stay REGISTERED — an unregistered scheduled task is a
    worker error on every fire — while doing nothing."""
    from apps.data.tasks import ingest_13f_current_quarter

    assert ingest_13f_current_quarter() == "removed"


def test_edgar_recent_filings_still_works():
    """The EDGAR *filings* provider is a different, live surface — untouched."""
    from unittest.mock import MagicMock

    from apps.data.models import FilingRecord
    from apps.data.providers.edgar import EdgarProvider

    as_of = dt.date(2026, 9, 7)
    for i, (form, filed) in enumerate(
        (("10-K", as_of - dt.timedelta(days=60)), ("10-Q", as_of - dt.timedelta(days=10)))
    ):
        FilingRecord.objects.create(
            ticker="AAPL", form_type=form, filed_at=filed, period_end=filed,
            accession=f"0000320193-26-1000{i}", url="https://sec/x",
            text_excerpt="body",
        )
    prov = EdgarProvider(user_agent="t t@t.com", http=MagicMock())
    out = prov.get_recent_filings(
        "AAPL", as_of=as_of, form_types=["10-K", "10-Q"], limit=2
    )
    assert [f.form_type for f in out] == ["10-Q", "10-K"]


def test_fundamentals_says_ownership_needs_fmp_ultimate_when_empty(monkeypatch):
    """With no entitlement the resolver returns None; the node must SAY so
    rather than leaving the ownership fields silently at their defaults."""
    from hedgefund_agents.analytical import fundamentals as fnode
    from hedgefund_agents.llm.client import LLMResponse
    from hedgefund_agents.outputs import FundamentalsOutput

    parsed = FundamentalsOutput(
        revenue_cagr_3y=0.1, gross_margin=0.4, operating_margin=0.3,
        fcf_margin=0.2, roic=0.15, debt_to_equity=0.5, quality_score=80,
        notes="ok",
    )

    def fake_call_structured(client, *, model, schema, messages, **kw):
        return parsed, LLMResponse(
            text="{}", model=model, provider="fake",
            prompt_tokens=1, completion_tokens=1, cost_usd=0.0,
        )

    monkeypatch.setattr(fnode, "call_structured", fake_call_structured)
    monkeypatch.setattr(fnode, "get_llm", lambda *a, **k: object())
    monkeypatch.setattr(fnode, "record_llm_call", lambda **kw: None)
    monkeypatch.setattr("apps.backtests.cache.make_cache_ctx", lambda *a, **k: None)

    from apps.data.interfaces import FundamentalRow

    class _Data:
        def get_fundamentals(self, ticker, metrics, *, as_of, lookback_quarters=8):
            from decimal import Decimal

            return [
                FundamentalRow(
                    ticker=ticker, as_of_date=as_of,
                    period_end=as_of - dt.timedelta(days=90 * i),
                    metric="revenue", value=Decimal(100 + i),
                )
                for i in range(8)
            ]

    from apps.data.providers.ownership import NOT_ENTITLED_REASON, OwnershipResolver

    state = {
        "ticker": "AAPL",
        "as_of_date": dt.date(2026, 9, 7),
        "data_provider": _Data(),
        "ownership_provider": OwnershipResolver(fmp=None),
    }
    out = fnode.run_fundamentals(state)["fundamentals"]
    assert out["smart_money_note"] == NOT_ENTITLED_REASON
    assert "FMP Ultimate" in out["smart_money_note"]
    assert out["institutional_ownership_pct"] is None
    assert out["institutional_ownership_trend"] == "unknown"
