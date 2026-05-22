"""Build run-level evidence and risk-context blobs (P01/P02a review).

`evidence` is a first-class provenance trail for run outputs — which
provider produced which row, when, and what URL/identifier backs it.
`risk_context` is an explicit label on whether sizing is illustrative
(stub portfolio), portfolio-aware (real), or research-only.

Both blobs are intentionally JSON-shaped (not new tables) so the UI can
render them without joining across many models. They're read-only once
the run finishes.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.utils import timezone


def _iso(d: dt.date | dt.datetime | None) -> str | None:
    if d is None:
        return None
    if isinstance(d, dt.datetime):
        return d.isoformat()
    return d.isoformat()


def _has_provider_key(key_name: str) -> bool:
    return bool(getattr(settings, key_name, ""))


def build_evidence(ticker: str, as_of: dt.date) -> dict[str, Any]:
    """Gather provenance items for a finished single-ticker run.

    Reads only persisted data; never re-fetches providers. Returns the
    shape consumed by the run-detail UI:

        {
          "as_of": "2024-12-31",
          "providers": {fmp: ok, fred: ok, tiingo: missing, edgar: ok, anthropic: ok},
          "items": [
            {"agent", "source", "provider", "url", "as_of",
             "retrieved_at", "label"}
          ]
        }

    Best-effort: any data-layer exception degrades that section to an
    empty list rather than failing the run.
    """
    items: list[dict[str, Any]] = []

    # Fundamentals — most-recent point-in-time row per metric.
    try:
        from apps.data.models import Fundamental

        rows = (
            Fundamental.objects.filter(
                ticker=ticker.upper(), as_of_date__lte=as_of
            )
            .order_by("metric", "-period_end")
            .distinct("metric")
        )
        # SQLite (test) doesn't support distinct(field). Fall back manually.
        try:
            sample = list(rows[:8])
        except Exception:
            seen: set[str] = set()
            sample = []
            qs = Fundamental.objects.filter(
                ticker=ticker.upper(), as_of_date__lte=as_of
            ).order_by("-period_end")[:64]
            for r in qs:
                if r.metric in seen:
                    continue
                seen.add(r.metric)
                sample.append(r)
                if len(sample) >= 8:
                    break
        for row in sample:
            items.append(
                {
                    "agent": "fundamentals",
                    "label": f"{row.metric} {row.period_end.isoformat()}",
                    "provider": row.source,
                    "source": "statement",
                    "url": "",
                    "as_of": _iso(row.as_of_date),
                    "retrieved_at": _iso(row.fetched_at),
                }
            )
    except Exception:
        pass

    # 10-K / 10-Q filings (used by news + persona agents).
    try:
        from apps.data.models import FilingRecord

        for f in FilingRecord.objects.filter(
            ticker=ticker.upper(), filed_at__lte=as_of
        ).order_by("-filed_at")[:3]:
            items.append(
                {
                    "agent": "news_digest",
                    "label": f"{f.form_type} filed {f.filed_at.isoformat()}",
                    "provider": "edgar",
                    "source": f.accession,
                    "url": f.url,
                    "as_of": _iso(f.filed_at),
                    "retrieved_at": _iso(f.fetched_at),
                }
            )
    except Exception:
        pass

    # News items consumed by the digest.
    try:
        from apps.data.models import NewsItem

        cutoff = as_of - dt.timedelta(days=30)
        for n in NewsItem.objects.filter(
            ticker=ticker.upper(),
            published_at__date__lte=as_of,
            published_at__date__gte=cutoff,
        ).order_by("-published_at")[:8]:
            items.append(
                {
                    "agent": "news_digest",
                    "label": n.headline[:140],
                    "provider": n.provider,
                    "source": n.source,
                    "url": n.url,
                    "as_of": _iso(n.published_at),
                    "retrieved_at": _iso(n.fetched_at),
                }
            )
    except Exception:
        pass

    # Macro snapshot — provides the series-used and fetch timestamp.
    try:
        from apps.data.models import MacroSnapshot

        snap = (
            MacroSnapshot.objects.filter(as_of_date__lte=as_of)
            .order_by("-as_of_date")
            .first()
        )
        if snap is not None:
            items.append(
                {
                    "agent": "macro",
                    "label": f"FRED snapshot {snap.as_of_date.isoformat()}",
                    "provider": "fred",
                    "source": "macro_snapshot",
                    "url": "",
                    "as_of": _iso(snap.as_of_date),
                    "retrieved_at": _iso(snap.created_at),
                }
            )
    except Exception:
        pass

    return {
        "as_of": _iso(as_of),
        "providers": {
            "fmp": "configured" if _has_provider_key("FMP_API_KEY") else "missing",
            "fred": "configured" if _has_provider_key("FRED_API_KEY") else "missing",
            "tiingo": "configured" if _has_provider_key("TIINGO_API_KEY") else "missing",
            "anthropic": "configured" if _has_provider_key("ANTHROPIC_API_KEY") else "missing",
            "openrouter": "configured" if _has_provider_key("OPENROUTER_API_KEY") else "missing",
            "edgar": "configured",  # public, no key
        },
        "items": items,
        "generated_at": _iso(timezone.now()),
    }


def build_risk_context(*, portfolio: Any | None = None) -> dict[str, Any]:
    """Label whether sizing used a stub portfolio, real portfolio, or none.

    The ad-hoc single-ticker council currently runs against an implicit
    $100K stub portfolio (see hedgefund_agents/risk/risk_manager.py). The
    UI shows this so users don't treat target_quantity as portfolio-grade
    advice.
    """
    if portfolio is None:
        return {
            "mode": "stub",
            "portfolio_id": None,
            "stub_nav_usd": "100000",
            "notes": (
                "Single-ticker research run. Target sizing is illustrative "
                "against a $100K stub portfolio — not portfolio-aware. Run "
                "via a Strategy to size against your real book."
            ),
        }
    return {
        "mode": "real",
        "portfolio_id": getattr(portfolio, "id", None),
        "stub_nav_usd": None,
        "notes": "Sized against a real portfolio.",
        "cash_balance_usd": str(getattr(portfolio, "cash_balance", Decimal("0"))),
    }
