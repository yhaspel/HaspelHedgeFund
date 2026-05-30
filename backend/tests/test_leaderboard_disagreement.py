"""P3b leaderboard follow-ups: disagreement-value ("useful contrarians") agent
metric, and the council-alpha drill-down series + endpoint."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.data.models import DailyBar
from apps.leaderboard import compute
from apps.leaderboard.models import AgentScorecard
from apps.portfolios.models import Portfolio, PortfolioStrategy, PortfolioTarget, Universe
from apps.runs.models import AgentMessage, Run

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="lbf@x.test", password="pw-fake-123456789")


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


def _council_run(user, ticker, signals: dict[str, str], as_of, confidence=70):
    """One run with several personas voting on the same ticker."""
    run = Run.objects.create(
        user=user, tickers=[ticker], as_of_date=as_of, status=Run.DONE,
        model_overrides={p: "m1" for p in signals},
        agent_versions={p: "v1" for p in signals},
    )
    for persona, sig in signals.items():
        AgentMessage.objects.create(
            run=run, agent_name=persona,
            parsed_output={"signal": sig, "confidence": confidence},
        )
    return run


def _strategy(user, kind=PortfolioStrategy.KIND_LONG_SHORT):
    universe = Universe.objects.create(name="lbf-uni", is_active=True)
    portfolio = Portfolio.objects.create(
        user=user, name="lbf-book", kind="strategy", cash_balance=Decimal("100000"),
    )
    return PortfolioStrategy.objects.create(
        user=user, name="lbf-strat", kind=kind, universe=universe, portfolio=portfolio,
    )


# ---------- disagreement value (#3) ----------


def test_annotate_contrarian_uses_majority():
    decisions = [
        {"run_id": 1, "signal": "bullish", "directional": True},
        {"run_id": 1, "signal": "bullish", "directional": True},
        {"run_id": 1, "signal": "bearish", "directional": True},   # against bullish majority
        {"run_id": 1, "signal": "neutral", "directional": False},  # not directional
    ]
    compute._annotate_contrarian(decisions)
    assert decisions[0]["contrarian"] is False
    assert decisions[2]["contrarian"] is True
    assert decisions[3]["contrarian"] is False


def test_contrarian_wrong_scores_zero(user):
    # AAPL +5% over 5d; consensus bullish; burry dissents bearish and is wrong.
    as_of = dt.date(2026, 5, 1)
    _bars("AAPL", as_of, [100, 101, 102, 103, 104, 105])
    _council_run(
        user, "AAPL",
        {"buffett": "bullish", "munger": "bullish", "burry": "bearish"}, as_of,
    )
    today = timezone.localdate()
    compute.recompute_agents(today)

    burry = AgentScorecard.objects.get(
        agent_name="burry", model_id="m1", window="lifetime", as_of=today
    )
    assert burry.n_contrarian_decisions == 1
    assert float(burry.contrarian_hit_rate) == 0.0
    # A consensus-aligned persona has no contrarian decisions.
    buffett = AgentScorecard.objects.get(
        agent_name="buffett", model_id="m1", window="lifetime", as_of=today
    )
    assert buffett.n_contrarian_decisions == 0
    assert buffett.contrarian_hit_rate is None


def test_contrarian_right_scores_one(user):
    # AAPL -5%; consensus bullish; burry dissents bearish and is right.
    as_of = dt.date(2026, 5, 1)
    _bars("AAPL", as_of, [100, 99, 98, 97, 96, 95])
    _council_run(
        user, "AAPL",
        {"buffett": "bullish", "munger": "bullish", "burry": "bearish"}, as_of,
    )
    today = timezone.localdate()
    compute.recompute_agents(today)

    burry = AgentScorecard.objects.get(
        agent_name="burry", model_id="m1", window="lifetime", as_of=today
    )
    assert burry.n_contrarian_decisions == 1
    assert float(burry.contrarian_hit_rate) == 1.0
    assert burry.contrarian_hit_rate_ci_low is not None  # Wilson CI populated


# ---------- council-alpha drill-down series (#6) ----------


def test_council_alpha_series_cumulative_and_skips_unpaired(user):
    s = _strategy(user)
    base = dt.date(2026, 1, 5)
    now = timezone.now().isoformat()
    for i, (rv, bv) in enumerate([("1.0", "0.4"), ("2.0", "0.5")]):
        PortfolioTarget.objects.create(
            strategy=s, as_of_date=base + dt.timedelta(days=i), status=PortfolioTarget.DONE,
            target_weights={"AAPL": 0.1},
            marked_snapshot={"since_as_of_pct": rv, "snapshot_at": now},
            baseline_weights={"AAPL": 0.1},
            baseline_marked_snapshot={"since_as_of_pct": bv, "snapshot_at": now},
        )
    # Cycle with no captured baseline → excluded from the series.
    PortfolioTarget.objects.create(
        strategy=s, as_of_date=base + dt.timedelta(days=5), status=PortfolioTarget.DONE,
        target_weights={"AAPL": 0.1},
        marked_snapshot={"since_as_of_pct": "9.9", "snapshot_at": now},
    )
    rows = compute.council_alpha_series(s)
    assert len(rows) == 2
    assert rows[0]["realised_pct"] == 1.0 and rows[0]["baseline_pct"] == 0.4
    # cumulative realised = (1.01 * 1.02 - 1) * 100 ≈ 3.02
    assert rows[1]["cum_realised_pct"] == pytest.approx(3.02, abs=0.01)
    assert rows[1]["cum_baseline_pct"] == pytest.approx(0.90, abs=0.01)


def test_council_alpha_endpoint_ok(user, auth_client):
    s = _strategy(user)
    now = timezone.now().isoformat()
    PortfolioTarget.objects.create(
        strategy=s, as_of_date=dt.date(2026, 1, 5), status=PortfolioTarget.DONE,
        target_weights={"AAPL": 0.1},
        marked_snapshot={"since_as_of_pct": "1.0", "snapshot_at": now},
        baseline_weights={"AAPL": 0.1},
        baseline_marked_snapshot={"since_as_of_pct": "0.4", "snapshot_at": now},
    )
    resp = auth_client.get(f"/api/leaderboard/strategies/{s.id}/council-alpha/?window=lifetime")
    assert resp.status_code == 200
    assert resp.data["strategy_id"] == s.id
    assert len(resp.data["rows"]) == 1


def test_council_alpha_endpoint_other_users_strategy_404(user, auth_client):
    other = User.objects.create_user(email="other-lbf@x.test", password="pw-fake-123456789")
    s = _strategy(other)
    resp = auth_client.get(f"/api/leaderboard/strategies/{s.id}/council-alpha/?window=lifetime")
    assert resp.status_code == 404
