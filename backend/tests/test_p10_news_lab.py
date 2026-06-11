"""P10 §E — the news-sentiment lab, instrumented honestly.

E1: the marking desync — realised + baseline snapshots refreshed in the SAME
pass; alpha ≡ 0 on byte-identical books. E2: the conviction overlay is
metered (LLMCall rows attach to the cycle target). E3: scheduled,
symbol-targeted news fetch + frozen-model classify, decoupled from page views.
E4: name-level decision grading vs forward returns. E5: the sentiment-free
equal-weight baseline marks alongside the sleeve.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.data.models import DailyBar, MarketNewsItem
from apps.leaderboard import compute
from apps.leaderboard.news_decisions import (
    news_decision_scoreboard,
    score_news_decisions,
)
from apps.portfolios import cycle_mark
from apps.portfolios.models import (
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    Universe,
    UniverseMembership,
)

User = get_user_model()

AS_OF = dt.date(2026, 5, 4)


@pytest.fixture
def user(db):
    return User.objects.create_user(email="p10e@x.test", password="pw-fake-123456789")


def _strategy(user, name="lab", kind=PortfolioStrategy.KIND_NEWS_SENTIMENT):
    u = Universe.objects.create(name=f"p10e-uni-{name}")
    for t in ("AAA", "BBB", "CCC", "DDD"):
        UniverseMembership.objects.create(
            universe=u, ticker=t, effective_from=dt.date(2024, 1, 1),
        )
    pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_STRATEGY, name=name)
    return PortfolioStrategy.objects.create(
        user=user, name=name, universe=u, portfolio=pf, kind=kind,
    )


def _bars(ticker, start, days, daily):
    px = 100.0
    for i in range(days):
        d = start + dt.timedelta(days=i)
        DailyBar.objects.create(
            ticker=ticker, date=d, open=px, high=px, low=px,
            close=Decimal(str(round(px, 4))),
            adjusted_close=Decimal(str(round(px, 4))),
            volume=1000, source="test",
        )
        px *= 1 + daily


# ---------------------------------------------------------------------------
# E1 — identical books ⇒ alpha exactly 0 (the +31bp phantom is dead).
# ---------------------------------------------------------------------------
def test_identical_books_produce_zero_alpha(db, user, monkeypatch):
    class _Bar:
        def __init__(self, d, c):
            self.date, self.close = d, c

    class _Provider:
        def get_daily_bars(self, ticker, start, end, as_of=None):
            price = 100.0 if end == AS_OF else 110.0
            return [_Bar(end, price)]

    monkeypatch.setattr(cycle_mark, "get_fmp_provider", lambda user=None: _Provider())
    s = _strategy(user)
    stale = (timezone.now() - dt.timedelta(days=2)).isoformat()
    targets = []
    for i in range(30):
        # Byte-identical books; the realised snapshot was stamped DAYS earlier
        # (the page-view path) with a DIFFERENT mark — the audit's exact bug.
        targets.append(PortfolioTarget.objects.create(
            strategy=s, as_of_date=AS_OF + dt.timedelta(days=i),
            status=PortfolioTarget.DONE,
            target_weights={"AAA": 0.5},
            baseline_weights={"AAA": 0.5},
            marked_snapshot={"since_as_of_pct": "-0.25", "snapshot_at": stale},
            baseline_marked_snapshot={},
        ))
    realised, baseline = compute._paired_returns(targets)
    assert len(realised) == 30
    assert realised == baseline                      # marked in the same pass
    out = compute._council_alpha(targets, nav=100_000.0, council_cost=0.0)
    assert out["alpha_bps"] == 0.0


def test_same_pass_pairs_are_read_as_is(db, user):
    """Pairs already stamped together are trusted (no provider hit) — the
    nightly recompute stays cheap for already-consistent history."""
    now = timezone.now().isoformat()
    t = SimpleNamespace(
        baseline_weights={"AAA": 0.5},
        marked_snapshot={"since_as_of_pct": "1.0", "snapshot_at": now},
        baseline_marked_snapshot={"since_as_of_pct": "0.5", "snapshot_at": now},
    )
    realised, baseline = compute._paired_returns([t])
    assert realised == [pytest.approx(0.01)]
    assert baseline == [pytest.approx(0.005)]


# ---------------------------------------------------------------------------
# E2 — the conviction overlay is metered.
# ---------------------------------------------------------------------------
def test_news_conviction_records_llm_calls(db, user, monkeypatch):
    from apps.portfolios import news_sentiment as ns
    from hedgefund_agents.models import LLMCall

    s = _strategy(user)
    target = PortfolioTarget.objects.create(
        strategy=s, as_of_date=AS_OF, status=PortfolioTarget.RUNNING,
    )
    MarketNewsItem.objects.create(
        provider="fmp", headline="AAA beats expectations", url="https://x/a1",
        source="t",
        published_at=timezone.make_aware(
            dt.datetime.combine(AS_OF, dt.time(12))   # inside the as-of window
        ),
        symbols=["AAA"], dedup_key="k1",
    )

    class _Parsed:
        conviction = 0.8
        keep = True
        rationale = "ok"

    resp = SimpleNamespace(
        provider="openrouter", model="m", prompt_tokens=10, cached_tokens=0,
        completion_tokens=5, cost_usd=0.0001, latency_ms=5,
    )
    monkeypatch.setattr(ns, "news_conviction", ns.news_conviction)  # keep ref
    monkeypatch.setattr(
        "hedgefund_agents.llm.structured.call_structured",
        lambda *a, **k: (_Parsed(), resp),
    )
    monkeypatch.setattr(
        "hedgefund_agents.registry.get_llm", lambda *a, **k: object(),
    )
    out = ns.news_conviction(
        ["AAA"], AS_OF, user_id=user.id, portfolio_target_id=target.pk,
    )
    assert out["AAA"] == pytest.approx(0.8)
    call = LLMCall.objects.get(portfolio_target=target)
    assert call.agent_name == "news_conviction"
    target.refresh_from_db()
    assert target.total_cost_usd == Decimal("0.000100")


# ---------------------------------------------------------------------------
# E3 — scheduled symbol-targeted fetch + frozen-model classify.
# ---------------------------------------------------------------------------
def test_fetch_lab_news_targets_universe_and_classifies(db, user, monkeypatch):
    from apps.portfolios import tasks_lab

    _strategy(user)
    fetched_symbols: list[list[str]] = []

    class _FakeProvider:
        def fetch_for_symbols(self, symbols, *, limit=100):
            fetched_symbols.append(sorted(symbols))
            return [
                MarketNewsItem(
                    provider="fmp", headline=f"{symbols[0]} news", url="https://x/n1",
                    source="t", published_at=timezone.now(), symbols=[symbols[0]],
                )
            ]

    classified: list[tuple[list, str]] = []

    def _fake_classify(rows, *, model_id, user_id):
        classified.append((list(rows), model_id))
        return True, None

    monkeypatch.setattr(
        tasks_lab, "fetch_lab_news", tasks_lab.fetch_lab_news)  # keep task ref
    monkeypatch.setattr(
        "apps.data.providers.factory.get_market_news_fmp_provider",
        lambda user=None, **kw: _FakeProvider(),
    )
    monkeypatch.setattr("apps.data.market_news_sentiment.classify", _fake_classify)
    out = tasks_lab.fetch_lab_news()
    assert fetched_symbols == [["AAA", "BBB", "CCC", "DDD"]]
    assert out["fetched"] == 1
    assert classified, "unscored lab-universe rows must be classified"
    rows, model_id = classified[0]
    assert model_id  # the frozen settings model, never user prefs
    from django.conf import settings
    assert model_id == settings.NEWS_LAB_SENTIMENT_MODEL


def test_run_news_lab_cycles_dispatches_each_sleeve(db, user, monkeypatch):
    from apps.portfolios import tasks_lab

    s1 = _strategy(user, name="lab1")
    _strategy(user, name="not-lab", kind=PortfolioStrategy.KIND_RISK_PARITY)
    archived = _strategy(user, name="lab-archived")
    archived.is_active = False
    archived.save(update_fields=["is_active"])

    ran: list[int] = []
    monkeypatch.setattr(tasks_lab, "fetch_lab_news", lambda: {"fetched": 0, "scored": 0})
    monkeypatch.setattr(
        "apps.portfolios.tasks.daily_long_short_cycle",
        lambda pk, force=False, **kw: ran.append(pk) or {"status": "done"},
    )
    out = tasks_lab.run_news_lab_cycles()
    assert ran == [s1.pk]                       # active news_sentiment only
    assert len(out["cycles"]) == 1


# ---------------------------------------------------------------------------
# E4 — name-level decision grading.
# ---------------------------------------------------------------------------
def _lab_target(s, *, conviction, ew=None):
    return PortfolioTarget.objects.create(
        strategy=s, as_of_date=AS_OF, status=PortfolioTarget.DONE,
        target_weights={"AAA": 0.5, "BBB": 0.5},
        baseline_weights={"AAA": 0.5, "BBB": 0.5},
        beta_diagnostics={
            "conviction": conviction,
            "candidates": list(conviction),
            "ew_baseline_weights": ew or {},
        },
    )


def test_score_news_decisions_grades_vs_basket(db, user):
    s = _strategy(user)
    # AAA strongly up (+1%/d), BBB flat, CCC down (−1%/d) over the window.
    _bars("AAA", AS_OF, 15, 0.01)
    _bars("BBB", AS_OF, 15, 0.0)
    _bars("CCC", AS_OF, 15, -0.01)
    t = _lab_target(s, conviction={"AAA": 0.9, "BBB": 0.5, "CCC": 0.1})
    out = score_news_decisions()
    assert out["scored"] == 1
    t.refresh_from_db()
    ds = t.beta_diagnostics["decision_scores"]
    per = ds["per_name"]
    assert per["AAA"]["graded"] and per["AAA"]["hit"] is True      # bullish, beat basket
    assert per["CCC"]["graded"] and per["CCC"]["hit"] is True      # bearish, trailed basket
    assert per["BBB"]["graded"] is False                           # neutral fallback skipped
    assert ds["n_graded"] == 2 and ds["n_hits"] == 2
    # Idempotent: a second run never re-grades.
    out2 = score_news_decisions()
    assert out2["scored"] == 0


def test_score_news_decisions_waits_for_forward_window(db, user):
    s = _strategy(user)
    _bars("AAA", AS_OF, 3, 0.01)        # only 3 bars — window incomplete
    t = _lab_target(s, conviction={"AAA": 0.9})
    out = score_news_decisions()
    assert out["scored"] == 0
    t.refresh_from_db()
    assert "decision_scores" not in t.beta_diagnostics


# ---------------------------------------------------------------------------
# E5 — the sentiment-free equal-weight baseline marks alongside the sleeve.
# ---------------------------------------------------------------------------
def test_ew_baseline_marked_and_scoreboard(db, user, client=None):
    s = _strategy(user)
    _bars("AAA", AS_OF, 15, 0.01)
    _bars("BBB", AS_OF, 15, 0.0)
    t = _lab_target(
        s, conviction={"AAA": 0.9, "BBB": 0.1},
        ew={"AAA": 0.5, "BBB": 0.5},
    )
    score_news_decisions()
    t.refresh_from_db()
    ew = t.beta_diagnostics["ew_baseline_mark"]
    assert ew["ew_book_pct"] is not None
    assert ew["news_book_pct"] is not None
    assert ew["sleeve_minus_ew_pct"] == pytest.approx(
        ew["news_book_pct"] - ew["ew_book_pct"], abs=1e-6,
    )
    board = news_decision_scoreboard(s)
    assert board["n_cycles"] == 1
    assert board["n_graded_decisions"] == 2
    assert board["hit_rate"] == 1.0
    assert board["n_sleeve_vs_ew_cycles"] == 1


def test_single_candidate_cycle_not_fake_graded(db, user):
    """A 1-candidate basket is degenerate (excess ≡ 0) — it must not mint a
    fake miss against itself."""
    s = _strategy(user)
    _bars("AAA", AS_OF, 15, 0.01)
    t = _lab_target(s, conviction={"AAA": 0.9})
    score_news_decisions()
    t.refresh_from_db()
    ds = t.beta_diagnostics["decision_scores"]
    assert ds["n_graded"] == 0
    assert ds["per_name"]["AAA"]["graded"] is False


def test_news_decisions_endpoint(db, user):
    from rest_framework.test import APIClient

    s = _strategy(user)
    c = APIClient()
    c.force_authenticate(user)
    r = c.get(f"/api/strategies/{s.id}/news-decisions/")
    assert r.status_code == 200
    assert r.json()["n_cycles"] == 0
    rp = _strategy(user, name="rp", kind=PortfolioStrategy.KIND_RISK_PARITY)
    r2 = c.get(f"/api/strategies/{rp.id}/news-decisions/")
    assert r2.status_code == 400
