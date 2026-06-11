"""News-sentiment single-name signal + long-only construction (P7c Part E4).

The council's only orthogonal edge is the news/language channel (the persona
*vote* is noise — research §4.7; ADR 0026). This sizes a long-only single-name
book off the pre-scored ``MarketNewsItem`` sentiment (recency-weighted), with an
optional bounded *persona conviction overlay* (the council in a language role,
not as the sizer). ``council_alpha`` measures the overlay vs the no-overlay
baseline forward — the honest test, since news can't be backtested (~1wk archive).
"""
from __future__ import annotations

import datetime as dt
import math

from .construction import RiskParityResult


def news_sentiment_scores(
    tickers: list[str], as_of: dt.date, *, lookback_days: int = 7, half_life_days: float = 3.0,
) -> dict[str, float]:
    """Recency-weighted mean ``sentiment_score`` ∈ [-1, 1] per ticker over the
    trailing ``lookback_days`` (exponential decay, ``half_life_days``). Only
    tickers with scored news in the window appear. Point-in-time: news strictly
    before ``as_of + 1 day`` (the cycle runs at the close of ``as_of``)."""
    from django.utils import timezone

    from apps.data.models import MarketNewsItem

    end = timezone.make_aware(dt.datetime.combine(as_of + dt.timedelta(days=1), dt.time.min))
    start = timezone.make_aware(
        dt.datetime.combine(as_of - dt.timedelta(days=lookback_days), dt.time.min)
    )
    tset = set(tickers)
    buckets: dict[str, list[tuple[dt.datetime, float]]] = {t: [] for t in tickers}
    rows = MarketNewsItem.objects.filter(
        published_at__gte=start, published_at__lt=end, sentiment_score__isnull=False,
    ).values_list("symbols", "published_at", "sentiment_score")
    for symbols, published_at, score in rows:
        for s in (symbols or []):
            if s in tset:
                buckets[s].append((published_at, float(score)))

    decay = math.log(2) / max(0.5, half_life_days)
    out: dict[str, float] = {}
    for ticker, items in buckets.items():
        if not items:
            continue
        num = den = 0.0
        for published_at, score in items:
            age_days = max(0.0, (end - published_at).total_seconds() / 86400.0)
            w = math.exp(-decay * age_days)
            num += w * score
            den += w
        if den > 0:
            out[ticker] = num / den
    return out


def construct_news_sentiment(
    scores: dict[str, float], sectors: dict[str, str] | None = None, *,
    top_n: int = 15, conviction: dict[str, float] | None = None, target_gross: float = 1.0,
) -> RiskParityResult:
    """Long-only top-N by news sentiment, equal-weighted to ``target_gross``.

    Positive-sentiment gate (long-only). ``conviction`` (the persona overlay, in
    [0, 1]) multiplies the SELECTION score and vetoes a name at 0; weighting stays
    equal (sentiment magnitude is noisy). With ``conviction=None`` this IS the
    council-free baseline ``council_alpha`` compares the overlaid book against.
    """
    sectors = sectors or {}
    conviction = conviction or {}
    eligible = {t: s for t, s in scores.items() if s > 0}              # long-only
    ranked = {t: s * float(conviction.get(t, 1.0)) for t, s in eligible.items()}
    ranked = {t: v for t, v in ranked.items() if v > 0}               # conviction veto at 0
    picks = sorted(ranked, key=lambda t: ranked[t], reverse=True)[: max(1, top_n)]
    if not picks:
        return RiskParityResult(
            target_weights={}, gross_pct=0.0, net_pct=0.0, sector_exposure={},
            rejected=[{"reason": "no_positive_sentiment_candidates"}],
            within_band=False, max_drift=1.0, diagnostics={"signal": "news_sentiment"},
        )
    w = {t: round(target_gross / len(picks), 6) for t in picks}
    sector_exposure: dict[str, float] = {}
    for t, weight in w.items():
        sec = sectors.get(t, "")
        sector_exposure[sec] = sector_exposure.get(sec, 0.0) + weight
    return RiskParityResult(
        target_weights=w,
        gross_pct=sum(w.values()),
        net_pct=sum(w.values()),
        sector_exposure=sector_exposure,
        rejected=[],
        within_band=False,
        max_drift=1.0,
        diagnostics={
            "signal": "news_sentiment", "holdings": len(picks),
            "mean_sentiment": round(sum(scores[t] for t in picks) / len(picks), 4),
        },
    )


# Default frugal model for the conviction overlay (reliable structured output,
# no reasoning — see the model-catalog memory). Cost-bounded to the candidates.
NEWS_CONVICTION_MODEL = "mistralai/mistral-small-3.2-24b-instruct"


def news_conviction(
    tickers: list[str], as_of: dt.date, *, model: str = NEWS_CONVICTION_MODEL,
    user_id: int | None = None, lookback_days: int = 7, max_headlines: int = 8,
    portfolio_target_id: int | None = None,
) -> dict[str, float]:
    """Bounded persona conviction overlay (the council in a LANGUAGE role, not the
    sizer): one frugal structured call per candidate reads its recent news → a
    conviction in [0, 1] (``keep=False`` → veto). Unscored failures / no-news fall
    back to 0.5 (neutral). Returns {ticker: conviction}.

    P10 §E2: every call is metered via ``record_llm_call`` threaded to
    ``portfolio_target_id`` — the scorecard's ``council_cost_usd`` was hardwired
    to $0.00 before this, making the net-of-cost half of the council-alpha
    verdict uncomputable."""
    import datetime as _dt

    from django.utils import timezone
    from pydantic import BaseModel

    from apps.data.models import MarketNewsItem
    from hedgefund_agents._persist import record_llm_call
    from hedgefund_agents.llm.client import Message
    from hedgefund_agents.llm.structured import call_structured
    from hedgefund_agents.registry import get_llm

    class _Conviction(BaseModel):
        conviction: float
        keep: bool
        rationale: str

    start = timezone.make_aware(
        _dt.datetime.combine(as_of - _dt.timedelta(days=lookback_days), _dt.time.min)
    )
    end = timezone.make_aware(_dt.datetime.combine(as_of + _dt.timedelta(days=1), _dt.time.min))
    system = (
        "You are a panel of investor-analysts judging ONE stock from its RECENT NEWS only. "
        "Decide whether the news supports holding a LONG position over the next few weeks. "
        "Output conviction in [0,1] (0=avoid, 1=strong), keep=false to veto, and a one-line "
        "rationale. Be skeptical: hype, already-priced moves, guidance cuts and "
        "litigation lower conviction."
    )
    client = get_llm("openrouter", user_id=user_id)
    # One window query, grouped in Python (symbols is a JSONField list —
    # `__contains` is Postgres-only, and per-ticker queries were N round-trips).
    tset = set(tickers)
    by_ticker: dict[str, list[tuple[str, str | None]]] = {t: [] for t in tickers}
    window = MarketNewsItem.objects.filter(
        published_at__gte=start, published_at__lt=end,
    ).order_by("-published_at").values_list("symbols", "headline", "sentiment")
    for symbols, headline, sentiment in window.iterator():
        for sym in (symbols or []):
            if sym in tset and len(by_ticker[sym]) < max_headlines:
                by_ticker[sym].append((headline, sentiment))
    out: dict[str, float] = {}
    for ticker in tickers:
        heads = by_ticker[ticker]
        if not heads:
            out[ticker] = 0.5
            continue
        user = f"Ticker {ticker}. Recent headlines:\n" + "\n".join(
            f"- [{s or '?'}] {h}" for h, s in heads
        )
        try:
            parsed, resp = call_structured(
                client, model=model, schema=_Conviction,
                messages=[Message("system", system), Message("user", user)],
                max_tokens=200, temperature=0.2,
            )
            try:
                record_llm_call(
                    run_id=None, agent_name="news_conviction", resp=resp,
                    portfolio_target_id=portfolio_target_id,
                )
            except Exception:  # noqa: BLE001 — metering must not abort the overlay
                pass
            out[ticker] = 0.0 if not parsed.keep else float(max(0.0, min(1.0, parsed.conviction)))
        except Exception:  # noqa: BLE001 — a bad name shouldn't abort the overlay
            out[ticker] = 0.5
    return out
