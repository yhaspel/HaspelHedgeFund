"""Council-alpha (P3b follow-up): the council-free deterministic baseline, the
generalized cycle-mark, and the nightly alpha/dollar-value compute."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.leaderboard import compute
from apps.leaderboard.council_alpha import baseline_target_weights
from apps.leaderboard.models import StrategyScorecard
from apps.portfolios import cycle_mark
from apps.portfolios.construction import Constraints
from apps.portfolios.models import (
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    Universe,
)

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="ca@x.test", password="pw-fake-123456789")


def _ranking(longs, shorts=None):
    return SimpleNamespace(long_candidates=longs, short_candidates=shorts or [])


# ---------- baseline construction (pure, no DB) ----------


def test_baseline_long_short_is_equal_weight_no_veto():
    strat = SimpleNamespace(kind=PortfolioStrategy.KIND_LONG_SHORT)
    cons = Constraints(
        target_gross_pct=1.0, target_net_pct=0.0, max_position_pct=1.0,
        max_sector_pct=1.0, min_position_pct=0.0,
    )
    ranking = _ranking(
        [{"ticker": "AAPL", "sector": "tech", "score": 0.9},
         {"ticker": "MSFT", "sector": "tech", "score": 0.1}],
        [{"ticker": "XYZ", "sector": "fin", "score": 0.5}],
    )
    w = baseline_target_weights(strat, ranking, cons)
    # Equal-weight: the two longs get equal magnitude despite different scores.
    assert w["AAPL"] == pytest.approx(w["MSFT"])
    assert w["XYZ"] < 0  # short side is negative


def test_baseline_sector_rotation_is_score_weighted():
    strat = SimpleNamespace(
        kind=PortfolioStrategy.KIND_SECTOR_ROTATION,
        target_gross_pct=1.0, per_etf_max_pct=1.0, per_etf_min_pct=0.0,
        max_etfs_held=6,
    )
    ranking = _ranking([
        {"ticker": "XLK", "sector": "tech", "score": 0.9},
        {"ticker": "XLF", "sector": "fin", "score": 0.1},
    ])
    w = baseline_target_weights(strat, ranking, Constraints())
    assert w["XLK"] > w["XLF"]  # higher composite score => higher weight


@pytest.mark.parametrize(
    "kind", [PortfolioStrategy.KIND_RISK_PARITY, PortfolioStrategy.KIND_PAIRS]
)
def test_baseline_deterministic_flavors_return_empty(kind):
    # Council-free flavors are their own baseline (handled by the cycle).
    strat = SimpleNamespace(kind=kind)
    ranking = _ranking([{"ticker": "A", "sector": "x", "score": 1.0}])
    assert baseline_target_weights(strat, ranking, Constraints()) == {}


# ---------- generalized cycle mark ----------


def test_compute_cycle_snapshot_marks_explicit_weights(monkeypatch):
    class _Bar:
        def __init__(self, d, c):
            self.date, self.close = d, c

    class _Provider:
        def get_daily_bars(self, ticker, start, end, as_of=None):
            # as_of leg (cycle date) -> 100; mark leg (any later date) -> 110.
            price = 100.0 if end == dt.date(2026, 1, 1) else 110.0
            return [_Bar(end, price)]

    monkeypatch.setattr(cycle_mark, "get_fmp_provider", lambda user=None: _Provider())
    target = SimpleNamespace(
        strategy=SimpleNamespace(user=None), as_of_date=dt.date(2026, 1, 1)
    )
    snap = cycle_mark.compute_cycle_snapshot(
        target, weights={"AAPL": 0.5}, on=dt.date(2026, 2, 1)
    )
    # +10% on a 0.5 (=50%) weight => +5.00pp.
    assert snap["since_as_of_pct"] == "5.00"


# ---------- nightly alpha + dollar value (pure, stubbed snapshots) ----------


def _stub_target(realised_pct: str, baseline_pct: str):
    now = timezone.now().isoformat()
    return SimpleNamespace(
        baseline_weights={"AAPL": 0.1},
        marked_snapshot={"since_as_of_pct": realised_pct, "snapshot_at": now},
        baseline_marked_snapshot={"since_as_of_pct": baseline_pct, "snapshot_at": now},
    )


def test_council_alpha_null_below_threshold():
    targets = [_stub_target("1.0", "0.5") for _ in range(5)]  # < 30 paired cycles
    out = compute._council_alpha(targets, nav=100_000.0, council_cost=10.0)
    assert out["alpha_bps"] is None
    assert out["net_value_usd"] is None
    assert out["cost_usd"] == 10.0  # cost surfaced even before alpha is meaningful


def test_council_alpha_positive_when_realised_beats_baseline():
    targets = [_stub_target("1.0", "0.5") for _ in range(30)]
    out = compute._council_alpha(targets, nav=100_000.0, council_cost=50.0)
    assert out["alpha_bps"] is not None and out["alpha_bps"] > 0
    assert out["net_value_usd"] is not None and out["net_value_usd"] > 0


def test_council_alpha_negative_when_baseline_beats_realised():
    targets = [_stub_target("0.2", "0.8") for _ in range(30)]
    out = compute._council_alpha(targets, nav=100_000.0, council_cost=50.0)
    assert out["alpha_bps"] is not None and out["alpha_bps"] < 0
    assert out["net_value_usd"] is not None and out["net_value_usd"] < 0


# ---------- end-to-end recompute ----------


def _strategy(user, kind=PortfolioStrategy.KIND_LONG_SHORT):
    universe = Universe.objects.create(name="ca-uni", is_active=True)
    portfolio = Portfolio.objects.create(
        user=user, name="ca-book", kind="strategy", cash_balance=Decimal("100000"),
    )
    return PortfolioStrategy.objects.create(
        user=user, name="ca-strat", kind=kind, universe=universe, portfolio=portfolio,
    )


def test_recompute_strategies_populates_council_alpha(user):
    s = _strategy(user)
    base = dt.date(2026, 1, 5)
    now = timezone.now().isoformat()
    # 30 paired cycles, realised (+1.0pp) consistently beating baseline (+0.4pp).
    for i in range(30):
        PortfolioTarget.objects.create(
            strategy=s, as_of_date=base + dt.timedelta(days=i),
            status=PortfolioTarget.DONE,
            target_weights={"AAPL": 0.1},
            marked_snapshot={"since_as_of_pct": "1.0", "snapshot_at": now},
            baseline_weights={"AAPL": 0.1},
            baseline_marked_snapshot={"since_as_of_pct": "0.4", "snapshot_at": now},
            baseline_version="v1",
        )
    today = timezone.localdate()
    compute.recompute_strategies(today)

    sc = StrategyScorecard.objects.get(strategy=s, window="lifetime", as_of=today)
    assert sc.council_alpha_bps is not None and float(sc.council_alpha_bps) > 0
    assert sc.council_cost_usd is not None  # surfaced (0 here — no LLM cost)
    assert sc.council_net_value_usd is not None and float(sc.council_net_value_usd) > 0
    assert sc.baseline_version == "v1"


def test_recompute_strategies_no_baseline_leaves_alpha_null(user):
    # Pre-council-alpha cycles (no baseline captured) keep council_alpha null
    # but still surface cost — forward-only, so nothing to diff against.
    s = _strategy(user)
    base = dt.date(2026, 1, 5)
    now = timezone.now().isoformat()
    for i in range(30):
        PortfolioTarget.objects.create(
            strategy=s, as_of_date=base + dt.timedelta(days=i),
            status=PortfolioTarget.DONE,
            target_weights={"AAPL": 0.1},
            marked_snapshot={"since_as_of_pct": "1.0", "snapshot_at": now},
        )
    today = timezone.localdate()
    compute.recompute_strategies(today)
    sc = StrategyScorecard.objects.get(strategy=s, window="lifetime", as_of=today)
    assert sc.council_alpha_bps is None
    assert sc.council_net_value_usd is None
