"""P7c Part E4 — news-sentiment single-name sleeve: the recency-weighted signal,
long-only construction, the persona conviction overlay, and the cycle wiring with
the council_alpha baseline (the E3 forward-validation harness)."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.data.models import DailyBar, MarketNewsItem
from apps.portfolios.models import (
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    Universe,
    UniverseMembership,
)
from apps.portfolios.news_sentiment import construct_news_sentiment, news_sentiment_scores

pytestmark = pytest.mark.django_db
AS_OF = dt.date(2026, 6, 8)
MEMBERS = [("AAA", "Tech"), ("BBB", "Tech"), ("CCC", "Fin")]


def _news(ticker: str, score: float, days_ago: int, *, n: int = 0) -> None:
    base = dt.datetime.combine(AS_OF, dt.time(12))
    when = timezone.make_aware(base - dt.timedelta(days=days_ago))
    MarketNewsItem.objects.create(
        provider="test", url=f"http://t/{ticker}/{days_ago}/{n}",
        headline=f"{ticker} news {days_ago}d", published_at=when,
        symbols=[ticker], sentiment="bullish" if score > 0 else "bearish",
        sentiment_score=score, dedup_key=f"{ticker}-{days_ago}-{n}",
    )


def test_news_sentiment_scores_recency_weighted():
    _news("AAA", 1.0, 0)       # fresh strong positive
    _news("AAA", -0.2, 6)      # stale mild negative (down-weighted)
    _news("BBB", -0.8, 1)      # negative
    _news("CCC", 0.5, 30)      # outside the 7-day window → ignored
    scores = news_sentiment_scores(["AAA", "BBB", "CCC", "DDD"], AS_OF)
    assert scores["AAA"] > 0.6                 # recency lifts the fresh +1.0 over the stale -0.2
    assert scores["BBB"] < 0
    assert "CCC" not in scores                 # outside window
    assert "DDD" not in scores                 # no news


def test_construct_long_only_top_n_positive_gate():
    scores = {"A": 0.9, "B": 0.6, "C": 0.3, "D": -0.5}
    res = construct_news_sentiment(scores, {"A": "Tech", "B": "Tech", "C": "Fin"}, top_n=2)
    assert set(res.target_weights) == {"A", "B"}        # top-2 by score
    assert "D" not in res.target_weights                # negative gated out (long-only)
    assert res.target_weights["A"] == res.target_weights["B"]   # equal-weighted
    assert sum(res.target_weights.values()) == pytest.approx(1.0)


def test_conviction_overlay_vetoes_and_reranks():
    scores = {"A": 0.9, "B": 0.6, "C": 0.5}
    # B vetoed (0), C boosted above A via conviction → selection changes.
    res = construct_news_sentiment(scores, top_n=2, conviction={"A": 0.2, "B": 0.0, "C": 1.0})
    assert "B" not in res.target_weights                # vetoed
    assert set(res.target_weights) == {"C", "A"}        # C (0.5*1.0) outranks A (0.9*0.2=0.18)
    # baseline (no overlay) would pick A,B
    base = construct_news_sentiment(scores, top_n=2, conviction=None)
    assert set(base.target_weights) == {"A", "B"}


def _strategy(user, **kw):
    u = Universe.objects.create(name="e4-uni")
    for t, sec in MEMBERS:
        UniverseMembership.objects.create(
            universe=u, ticker=t, sector=sec, effective_from=dt.date(2020, 1, 1)
        )
    pf = Portfolio.objects.create(
        user=user, kind=Portfolio.KIND_STRATEGY, name="e4", cash_balance=Decimal("100000")
    )
    defaults = dict(
        kind=PortfolioStrategy.KIND_NEWS_SENTIMENT, universe=u, portfolio=pf,
        target_gross_pct=Decimal("1.00"), max_positions=2, max_position_pct=Decimal("0.60"),
        min_trade_notional_usd=Decimal("100"), max_turnover_pct=Decimal("5.0"), personas=[],
    )
    defaults.update(kw)
    return PortfolioStrategy.objects.create(user=user, name="News Sentiment", **defaults)


def test_run_cycle_end_to_end_with_overlay(monkeypatch):
    from apps.portfolios import news_sentiment, tasks

    user = get_user_model().objects.create_user(email="e4@x.test", password="pw-fake-12345")
    for t in ("AAA", "BBB", "CCC"):
        DailyBar.objects.create(ticker=t, date=AS_OF, open=100, high=100, low=100,
                                close=100, adjusted_close=100, volume=1, source="fmp")
    _news("AAA", 0.9, 0)
    _news("BBB", 0.7, 0)
    _news("CCC", 0.5, 1)
    s = _strategy(user, enable_council_veto=True)

    class _Stub:
        def get_daily_bars(self, ticker, start, end, as_of):
            return list(DailyBar.objects.filter(ticker=ticker, date__lte=as_of))

    monkeypatch.setattr(tasks, "get_fmp_provider", lambda **kw: _Stub())
    # Bounded overlay: veto BBB, keep AAA/CCC.
    monkeypatch.setattr(
        news_sentiment, "news_conviction",
        lambda tickers, as_of, **kw: {t: (0.0 if t == "BBB" else 1.0) for t in tickers},
    )

    out = tasks._run_news_sentiment_cycle(s, AS_OF, MEMBERS)
    assert out["status"] == "done" and out["overlay"] is True
    target = PortfolioTarget.objects.get(pk=out["target_id"])
    assert set(target.target_weights) == {"AAA", "CCC"}    # BBB vetoed by the overlay
    # council_alpha baseline = no-overlay book (top-2 by raw sentiment = AAA, BBB)
    assert set(target.baseline_weights) == {"AAA", "BBB"}
    assert target.baseline_weights != target.target_weights   # overlay changed the book
    assert target.beta_diagnostics["overlay"] == "council"
    assert "BBB" in target.beta_diagnostics["vetoed"]


def test_run_cycle_overlay_off_baseline_equals_realised(monkeypatch):
    from apps.portfolios import tasks

    user = get_user_model().objects.create_user(email="e4b@x.test", password="pw-fake-12345")
    for t in ("AAA", "BBB", "CCC"):
        DailyBar.objects.create(ticker=t, date=AS_OF, open=100, high=100, low=100,
                                close=100, adjusted_close=100, volume=1, source="fmp")
    _news("AAA", 0.9, 0)
    _news("BBB", 0.7, 0)
    s = _strategy(user, enable_council_veto=False)

    class _Stub:
        def get_daily_bars(self, ticker, start, end, as_of):
            return list(DailyBar.objects.filter(ticker=ticker, date__lte=as_of))

    monkeypatch.setattr(tasks, "get_fmp_provider", lambda **kw: _Stub())
    out = tasks._run_news_sentiment_cycle(s, AS_OF, MEMBERS)
    target = PortfolioTarget.objects.get(pk=out["target_id"])
    assert out["overlay"] is False
    assert target.target_weights == target.baseline_weights   # no overlay → council_alpha ≈ 0
