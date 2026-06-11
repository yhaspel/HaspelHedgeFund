"""P10 §E4/§E5 — name-level decision scoring for the news-sentiment lab.

Book-level weekly overlay-vs-baseline diffs need ~200-800 weeks for
significance at plausible IRs; ~30 NAME-level decisions per cycle reach power
(55% vs 50% hit-rate, α=5%, 80%) in ~26-30 weekly cycles. This module grades
every persisted conviction/veto decision (``beta_diagnostics["conviction"]``,
written by the cycle) against the name's forward return relative to the
candidate basket, once the forward window has elapsed — and (§E5) marks the
sentiment-free equal-weight baseline book so the SLEEVE itself (not just the
overlay increment) is benchmarked against a $0 deterministic basket.

All marks here read DailyBar adjusted_close directly (point-in-time, $0).
Scores persist back into ``beta_diagnostics["decision_scores"]`` /
``["ew_baseline_mark"]`` (idempotent: a cycle is scored exactly once).
"""
from __future__ import annotations

import datetime as dt
import logging
import statistics

from django.utils import timezone

from apps.portfolios.models import PortfolioStrategy, PortfolioTarget

from .forward_returns import forward_return

log = logging.getLogger(__name__)

# Trading-day horizon for grading a decision (~2 calendar weeks).
DECISION_FORWARD_DAYS = 10
# Convictions inside 0.5 ± band are "no information" and aren't graded (0.5 is
# the no-news/error fallback).
NEUTRAL_BAND = 0.05


def _grade_target(target: PortfolioTarget) -> dict | None:
    """Grade one cycle's decisions; None when the forward window hasn't
    elapsed for the basket yet."""
    bd = target.beta_diagnostics or {}
    conviction: dict = bd.get("conviction") or {}
    if not conviction:
        return None
    fwd: dict[str, float] = {}
    for ticker in conviction:
        r = forward_return(ticker, target.as_of_date, DECISION_FORWARD_DAYS)
        if r is None:
            return None  # window incomplete — try again tomorrow
        fwd[ticker] = r
    if len(fwd) < 2:
        # A 1-candidate basket is degenerate (excess ≡ 0 by construction) —
        # record the cycle as graded-empty rather than minting a fake miss.
        return {
            "forward_days": DECISION_FORWARD_DAYS,
            "scored_at": timezone.now().isoformat(),
            "basket_fwd_pct": round(statistics.fmean(fwd.values()) * 100, 4) if fwd else None,
            "n_graded": 0,
            "n_hits": 0,
            "per_name": {
                t: {"conviction": c, "fwd_pct": round(fwd[t] * 100, 4),
                    "excess_pct": 0.0, "graded": False}
                for t, c in conviction.items()
            },
        }
    basket = statistics.fmean(fwd.values())
    per_name: dict[str, dict] = {}
    n_hits = n_graded = 0
    for ticker, c in conviction.items():
        excess = fwd[ticker] - basket
        if abs(float(c) - 0.5) <= NEUTRAL_BAND:
            per_name[ticker] = {
                "conviction": c, "fwd_pct": round(fwd[ticker] * 100, 4),
                "excess_pct": round(excess * 100, 4), "graded": False,
            }
            continue
        bullish = float(c) > 0.5
        hit = (excess > 0) == bullish
        n_graded += 1
        n_hits += 1 if hit else 0
        per_name[ticker] = {
            "conviction": c, "fwd_pct": round(fwd[ticker] * 100, 4),
            "excess_pct": round(excess * 100, 4), "graded": True, "hit": hit,
        }
    return {
        "forward_days": DECISION_FORWARD_DAYS,
        "scored_at": timezone.now().isoformat(),
        "basket_fwd_pct": round(basket * 100, 4),
        "n_graded": n_graded,
        "n_hits": n_hits,
        "per_name": per_name,
    }


def _ew_mark(target: PortfolioTarget) -> dict | None:
    """§E5: mark the sentiment-free equal-weight baseline book AND the
    no-overlay news book with the same bar-based machinery, so 'does LLM
    sentiment beat a $0 basket' is an apples-to-apples read."""
    bd = target.beta_diagnostics or {}
    ew = bd.get("ew_baseline_weights") or {}
    news_book = target.baseline_weights or {}
    if not ew:
        return None

    def _book_return_pct(weights: dict) -> float | None:
        rets = []
        for ticker in weights:
            r = forward_return(ticker, target.as_of_date, DECISION_FORWARD_DAYS)
            if r is not None:
                rets.append(r)
        # Equal-weight books: the mean return is the book return.
        return round(statistics.fmean(rets) * 100, 4) if rets else None

    ew_pct = _book_return_pct(ew)
    news_pct = _book_return_pct(news_book) if news_book else None
    if ew_pct is None:
        return None
    return {
        "forward_days": DECISION_FORWARD_DAYS,
        "scored_at": timezone.now().isoformat(),
        "ew_book_pct": ew_pct,
        "news_book_pct": news_pct,
        "sleeve_minus_ew_pct": (
            round(news_pct - ew_pct, 4) if news_pct is not None else None
        ),
    }


def score_news_decisions(today: dt.date | None = None) -> dict:
    """Nightly: grade every unscored news-lab cycle whose forward window has
    elapsed. Idempotent — a graded cycle is never re-graded."""
    scored = skipped = 0
    qs = PortfolioTarget.objects.filter(
        strategy__kind=PortfolioStrategy.KIND_NEWS_SENTIMENT,
        status=PortfolioTarget.DONE,
    ).order_by("as_of_date")
    for target in qs.iterator():
        bd = target.beta_diagnostics or {}
        changed = False
        if bd.get("conviction") and not bd.get("decision_scores"):
            grades = _grade_target(target)
            if grades is not None:
                bd["decision_scores"] = grades
                changed = True
        if bd.get("ew_baseline_weights") and not bd.get("ew_baseline_mark"):
            mark = _ew_mark(target)
            if mark is not None:
                bd["ew_baseline_mark"] = mark
                changed = True
        if changed:
            target.beta_diagnostics = bd
            target.save(update_fields=["beta_diagnostics"])
            scored += 1
        else:
            skipped += 1
    return {"scored": scored, "skipped": skipped}


def news_decision_scoreboard(strategy: PortfolioStrategy) -> dict:
    """Aggregate name-level scoreboard for one lab sleeve — the numbers the
    ADR-0027 kill criteria read (hit-rate over ≥500 graded decisions; sleeve
    vs the $0 equal-weight basket)."""
    targets = list(
        PortfolioTarget.objects.filter(
            strategy=strategy, status=PortfolioTarget.DONE,
        ).order_by("as_of_date")
    )
    n_graded = n_hits = 0
    excess: list[float] = []
    cycles = []
    sleeve_minus_ew: list[float] = []
    for t in targets:
        bd = t.beta_diagnostics or {}
        ds = bd.get("decision_scores") or {}
        if ds:
            n_graded += int(ds.get("n_graded") or 0)
            n_hits += int(ds.get("n_hits") or 0)
            excess.extend(
                row["excess_pct"]
                for row in (ds.get("per_name") or {}).values()
                if row.get("graded")
            )
        ew = bd.get("ew_baseline_mark") or {}
        if ew.get("sleeve_minus_ew_pct") is not None:
            sleeve_minus_ew.append(float(ew["sleeve_minus_ew_pct"]))
        cycles.append({
            "target_id": t.pk,
            "as_of_date": t.as_of_date.isoformat(),
            "overlay": (bd.get("overlay") or "none"),
            "n_graded": int(ds.get("n_graded") or 0) if ds else 0,
            "n_hits": int(ds.get("n_hits") or 0) if ds else 0,
            "ew_baseline_mark": ew or None,
        })
    return {
        "strategy_id": strategy.pk,
        "n_cycles": len(targets),
        "n_graded_decisions": n_graded,
        "hit_rate": round(n_hits / n_graded, 4) if n_graded else None,
        "mean_excess_pct": (
            round(statistics.fmean(excess), 4) if excess else None
        ),
        "sleeve_minus_ew_mean_pct": (
            round(statistics.fmean(sleeve_minus_ew), 4) if sleeve_minus_ew else None
        ),
        "n_sleeve_vs_ew_cycles": len(sleeve_minus_ew),
        "cycles": cycles,
    }
