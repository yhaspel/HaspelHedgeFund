"""Leaderboard: forward-return primitive, agent + strategy math, API (P3b)."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.data.models import DailyBar
from apps.leaderboard import compute
from apps.leaderboard.forward_returns import forward_return, wilson_interval
from apps.leaderboard.models import AgentScorecard, StrategyScorecard
from apps.portfolios.models import Portfolio, PortfolioStrategy, PortfolioTarget, Universe
from apps.runs.models import AgentMessage, Run

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="lb@x.test", password="pw-fake-123456789")


@pytest.fixture
def auth_client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _bars(ticker, start: dt.date, closes):
    for i, c in enumerate(closes):
        DailyBar.objects.create(
            ticker=ticker, date=start + dt.timedelta(days=i),
            open=c, high=c, low=c, close=c, adjusted_close=c, volume=1000, source="fmp",
        )


def _persona_run(user, ticker, persona, signal, confidence, as_of, model="m1", version="v1"):
    run = Run.objects.create(
        user=user, tickers=[ticker], as_of_date=as_of, status=Run.DONE,
        model_overrides={persona: model}, agent_versions={persona: version},
    )
    AgentMessage.objects.create(
        run=run, agent_name=persona,
        parsed_output={"signal": signal, "confidence": confidence},
    )
    return run


def _strategy(user, kind=PortfolioStrategy.KIND_LONG_SHORT):
    universe = Universe.objects.create(name="lb-uni", is_active=True)
    portfolio = Portfolio.objects.create(
        user=user, name="lb-book", kind="strategy", cash_balance=Decimal("100000"),
    )
    return PortfolioStrategy.objects.create(
        user=user, name="lb-strat", kind=kind, universe=universe, portfolio=portfolio,
    )


# ---------- forward_return ----------


def test_forward_return_basic(db):
    _bars("AAPL", dt.date(2026, 5, 1), [100, 101, 102, 103, 104, 105])
    assert forward_return("AAPL", dt.date(2026, 5, 1), 5) == pytest.approx(0.05)


def test_forward_return_insufficient_bars_returns_none(db):
    _bars("AAPL", dt.date(2026, 5, 1), [100, 101, 102])  # only 3 bars
    assert forward_return("AAPL", dt.date(2026, 5, 1), 5) is None


def test_wilson_interval_sane():
    lo, hi = wilson_interval(8, 10)
    assert 0 < lo < 0.8 < hi < 1.0


# ---------- agents ----------


def test_recompute_agents_hit_and_brier(user):
    as_of = dt.date(2026, 5, 1)
    _bars("AAPL", as_of, [100, 101, 102, 103, 104, 105])  # +5% over 5 days
    _persona_run(user, "AAPL", "buffett", "bullish", 80, as_of)
    today = timezone.localdate()
    compute.recompute_agents(today)

    sc = AgentScorecard.objects.get(
        agent_name="buffett", window="lifetime", as_of=today
    )
    assert sc.n_directional == 1
    assert float(sc.hit_rate) == 1.0  # bullish + up = hit
    assert float(sc.brier_score) == pytest.approx(0.04)  # (0.8-1)^2
    assert float(sc.avg_forward_return_bps) == pytest.approx(500.0)
    assert sc.model_id == "m1"
    assert sc.provisional is True  # n < 30


def test_recompute_agents_miss_lowers_hit_rate(user):
    as_of = dt.date(2026, 5, 1)
    _bars("AAPL", as_of, [100, 101, 102, 103, 104, 105])  # +5%
    _bars("MSFT", as_of, [100, 99, 98, 97, 96, 95])  # -5%
    _persona_run(user, "AAPL", "munger", "bullish", 70, as_of)   # hit
    _persona_run(user, "MSFT", "munger", "bullish", 70, as_of)   # miss (down)
    today = timezone.localdate()
    compute.recompute_agents(today)
    sc = AgentScorecard.objects.get(agent_name="munger", window="lifetime", as_of=today)
    assert sc.n_directional == 2
    assert float(sc.hit_rate) == 0.5


def test_agent_decision_detail(user):
    as_of = dt.date(2026, 5, 1)
    _bars("AAPL", as_of, [100, 101, 102, 103, 104, 105])
    _persona_run(user, "AAPL", "graham", "bullish", 60, as_of)
    rows = compute.agent_decision_detail("graham", window="lifetime")
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["forward_return_pct"] == pytest.approx(5.0)


# ---------- strategies ----------


def test_recompute_strategies_metrics(user):
    """Four cycles is not a Sharpe.

    Updated for wave 3: the old expectation (``sharpe is not None`` from four
    chained ``since_as_of_pct`` values) WAS the bug — those windows overlap and
    four points cannot support a ratio. The row now keeps the small-sample
    facts and nulls the ratios.
    """
    s = _strategy(user)
    base = dt.date(2026, 4, 1)
    # 100 -> 101 -> 100.5 -> 102.5 -> 103 over the five boundary dates.
    _bars("AAPL", base, [100, 101, 100.5, 102.5, 103])
    today = timezone.localdate()
    for i in range(4):
        PortfolioTarget.objects.create(
            strategy=s, as_of_date=base + dt.timedelta(days=i),
            status=PortfolioTarget.DONE,
            target_weights={"AAPL": 1.0},
            marked_snapshot={"since_as_of_pct": "1.0"},
        )
    compute.recompute_strategies(today)

    sc = StrategyScorecard.objects.get(strategy=s, window="lifetime", as_of=today)
    assert sc.n_cycles == 4
    assert sc.n_observations == 4
    # Disjoint chain telescopes to the real move: 103/100 - 1 = +3%.
    assert float(sc.total_return_pct) == pytest.approx(3.0, abs=0.01)
    assert sc.provisional is True  # < 20 observations
    assert sc.sharpe is None       # …so no ratio is offered
    assert sc.sortino is None
    assert sc.annualised_return_pct is None
    assert sc.hit_rate is not None and sc.max_drawdown_pct is not None
    assert sc.metrics_version == 2
    assert sc.council_alpha_bps is None  # deferred

    # flavor aggregate row exists, is owned by the user, and is provisional too
    flavor = StrategyScorecard.objects.get(
        strategy__isnull=True, flavor=s.kind, window="lifetime", as_of=today
    )
    assert flavor.user_id == user.id
    assert flavor.provisional is True
    assert flavor.sharpe is None


# ---------- API ----------


def test_leaderboard_api(user, auth_client):
    as_of = dt.date(2026, 5, 1)
    _bars("AAPL", as_of, [100, 101, 102, 103, 104, 105])
    _persona_run(user, "AAPL", "buffett", "bullish", 80, as_of)
    s = _strategy(user)
    PortfolioTarget.objects.create(
        strategy=s, as_of_date=as_of, status=PortfolioTarget.DONE,
        target_weights={"AAPL": 10.0}, marked_snapshot={"since_as_of_pct": "1.0"},
    )
    PortfolioTarget.objects.create(
        strategy=s, as_of_date=as_of + dt.timedelta(days=1), status=PortfolioTarget.DONE,
        target_weights={"AAPL": 12.0}, marked_snapshot={"since_as_of_pct": "0.5"},
    )

    r = auth_client.post("/api/leaderboard/recompute/")
    assert r.status_code == 200
    assert r.json()["agents"] >= 1

    agents = auth_client.get("/api/leaderboard/agents/?window=lifetime")
    assert agents.status_code == 200
    assert any(row["agent_name"] == "buffett" for row in agents.json()["rows"])

    strat = auth_client.get("/api/leaderboard/strategies/?window=lifetime")
    assert strat.status_code == 200
    assert len(strat.json()["rows"]) == 1

    flavor = auth_client.get("/api/leaderboard/strategies/by-flavor/?window=lifetime")
    assert flavor.status_code == 200
    assert len(flavor.json()["rows"]) >= 1

    decisions = auth_client.get("/api/leaderboard/agents/buffett/decisions/?window=lifetime")
    assert decisions.status_code == 200
    assert decisions.json()["decisions"][0]["ticker"] == "AAPL"
