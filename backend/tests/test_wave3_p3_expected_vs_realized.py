"""Wave 3 / WP P3 — expected (backtest fold) vs realized (live cycle) per cycle.

Covers the fold match (covering / nearest / none), the z-score + percentile off
the fold's out-of-sample daily distribution, the ``outside_distribution`` flag,
the pro-rated fallback when a fold persisted no daily series, and the honesty
contract: no gate-passing backtest / no covering fold / < MIN_SCORED_CYCLES ⇒
``provisional`` with null ratios instead of invented numbers.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.backtests.models import Backtest, BacktestDay, BacktestFold, BacktestMetrics
from apps.data.models import DailyBar
from apps.portfolios import expected_vs_realized as evr
from apps.portfolios.models import (
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    Universe,
    UniverseMembership,
)

User = get_user_model()

START = dt.date(2026, 1, 5)
UNIVERSE = ("AAPL",)


@pytest.fixture
def user(db):
    return User.objects.create_user(email="w3p3-evr@x.test", password="pw-fake-123456789")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _strategy(user, name="EVR"):
    u = Universe.objects.create(name=f"evr-uni-{name}")
    for t in UNIVERSE:
        UniverseMembership.objects.create(
            universe=u, ticker=t, sector="Tech", effective_from=dt.date(2020, 1, 1),
        )
    pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_STRATEGY, name=name)
    return PortfolioStrategy.objects.create(
        user=user, name=name, kind=PortfolioStrategy.KIND_LONG_ONLY, universe=u, portfolio=pf,
    )


def _bars(ticker: str, start: dt.date, closes, step_days: int = 1):
    for i, c in enumerate(closes):
        DailyBar.objects.create(
            ticker=ticker, date=start + dt.timedelta(days=i * step_days),
            open=c, high=c, low=c, close=c, adjusted_close=c, volume=1000, source="fmp",
        )


def _cycles(strategy, n: int, *, step_days: int = 7, weights=None):
    for i in range(n):
        PortfolioTarget.objects.create(
            strategy=strategy, as_of_date=START + dt.timedelta(days=i * step_days),
            status=PortfolioTarget.DONE, target_weights=weights or {"AAPL": 1.0},
        )


def _backtest(
    strategy, *, n_folds=8, oos_days=63, daily_series=True, mean_daily=0.001, sigma=0.01,
    passing=True,
):
    """A DONE total-return backtest whose folds' OOS windows blanket the cycles."""
    bt = Backtest.objects.create(
        user=strategy.user, strategy=strategy, name="validation",
        universe=list(UNIVERSE), start_date=dt.date(2024, 1, 1), end_date=dt.date(2027, 1, 1),
        engine_mode=Backtest.COUNCIL, status=Backtest.DONE,
        data_era=Backtest.ERA_TOTAL_RETURN,
    )
    BacktestMetrics.objects.create(
        backtest=bt,
        mean_oos_sharpe=Decimal("1.2") if passing else Decimal("-0.5"),
        sharpe=Decimal("0.9") if passing else Decimal("-0.4"),
        max_drawdown_pct=Decimal("3.0"),
    )
    # Folds tile forward from well before the first cycle.
    fold_start = START - dt.timedelta(days=oos_days)
    for i in range(n_folds):
        oos_start = fold_start + dt.timedelta(days=i * oos_days)
        oos_end = oos_start + dt.timedelta(days=oos_days - 1)
        fold = BacktestFold.objects.create(
            backtest=bt, fold_index=i,
            is_start=oos_start - dt.timedelta(days=252), is_end=oos_start - dt.timedelta(days=1),
            oos_start=oos_start, oos_end=oos_end,
            oos_return_pct=Decimal("5.0"), oos_sharpe=Decimal("1.1"),
        )
        if not daily_series:
            continue
        # A deterministic zig-zag with the requested mean/σ, so the fold's daily
        # distribution is exactly known to the test.
        value = 100.0
        for d in range(oos_days):
            step = mean_daily + (sigma if d % 2 == 0 else -sigma)
            BacktestDay.objects.create(
                backtest=bt, fold=fold, segment=BacktestDay.SEG_OOS,
                date=oos_start + dt.timedelta(days=d),
                cash=Decimal("0"), positions=[],
                portfolio_value=Decimal(str(round(value, 6))),
            )
            value *= 1.0 + step
    return bt


# ---------------------------------------------------------------------------
# Fold matching
# ---------------------------------------------------------------------------
def test_cycle_is_scored_against_the_fold_that_covers_its_as_of_date(user):
    strategy = _strategy(user)
    _bars("AAPL", START, [100 + i for i in range(80)])
    _cycles(strategy, 6)
    _backtest(strategy)

    out = evr.expected_vs_realized(strategy, end_date=START + dt.timedelta(days=45))
    assert out["backtest"]["gate_passed"] is True
    assert len(out["cycles"]) == 6
    for row in out["cycles"]:
        as_of = dt.date.fromisoformat(row["as_of_date"])
        assert row["fold"]["covers"] is True
        assert row["fold"]["gap_days"] == 0
        assert dt.date.fromisoformat(row["fold"]["oos_start"]) <= as_of
        assert as_of <= dt.date.fromisoformat(row["fold"]["oos_end"])
        assert row["expected_source"] == "fold_daily"
        assert row["z_score"] is not None


def test_a_cycle_outside_every_fold_window_uses_the_nearest_and_turns_provisional(user):
    strategy = _strategy(user)
    _bars("AAPL", START, [100 + i for i in range(400)])
    # One fold only, and it ends long before the cycles start.
    bt = _backtest(strategy, n_folds=1, oos_days=30)
    fold = bt.folds.first()
    _cycles(strategy, 6, step_days=7)

    out = evr.expected_vs_realized(strategy, end_date=START + dt.timedelta(days=60))
    later = [r for r in out["cycles"] if not r["fold"]["covers"]]
    assert later, "cycles after the last fold must be flagged, not silently matched"
    assert all(r["fold"]["index"] == fold.fold_index for r in later)
    assert all(r["fold"]["gap_days"] > 0 for r in later)
    assert all("nearest fold" in r["note"] for r in later)
    assert out["provisional"] is True
    assert any("outside every out-of-sample fold" in r for r in out["provisional_reasons"])
    # ...and the ratios are withheld rather than computed off indicative rows.
    assert out["summary"]["inside_ratio"] is None
    assert out["summary"]["outside_ratio"] is None
    assert out["summary"]["mean_gap_pp"] is None


def test_no_backtest_returns_the_realized_rows_with_null_expectations(user):
    strategy = _strategy(user)
    _bars("AAPL", START, [100 + i for i in range(40)])
    _cycles(strategy, 3)

    out = evr.expected_vs_realized(strategy, end_date=START + dt.timedelta(days=25))
    assert out["backtest"] is None
    assert out["provisional"] is True
    assert len(out["cycles"]) == 3
    assert all(r["realized_return_pct"] is not None for r in out["cycles"])
    assert all(r["expected_return_pct"] is None for r in out["cycles"])
    assert all(r["fold"] is None for r in out["cycles"])
    assert all("No validation backtest fold" in r["note"] for r in out["cycles"])
    assert out["summary"]["scored"] == 0
    assert out["summary"]["mean_gap_pp"] is None


def test_failing_gate_is_named_as_the_reason_the_payload_is_provisional(user):
    strategy = _strategy(user)
    _bars("AAPL", START, [100 + i for i in range(80)])
    _cycles(strategy, 6)
    _backtest(strategy, passing=False)

    out = evr.expected_vs_realized(strategy, end_date=START + dt.timedelta(days=45))
    assert out["backtest"]["gate_passed"] is False
    assert out["provisional"] is True
    assert any("validation gate does not pass" in r for r in out["provisional_reasons"])
    assert out["summary"]["inside_ratio"] is None


# ---------------------------------------------------------------------------
# The distribution maths
# ---------------------------------------------------------------------------
def test_a_realized_return_far_from_the_fold_mean_is_flagged_outside(user):
    strategy = _strategy(user)
    # A single cycle whose realized move is enormous (+100% over one week)
    # against a fold whose daily σ is 1%.
    _bars("AAPL", START, [100, 200], step_days=7)
    _cycles(strategy, 1)
    _backtest(strategy, sigma=0.01, mean_daily=0.0)

    out = evr.expected_vs_realized(strategy, end_date=START + dt.timedelta(days=7))
    row = out["cycles"][0]
    assert row["realized_return_pct"] == pytest.approx(100.0, abs=0.01)
    assert row["z_score"] > evr.Z_OUTSIDE
    assert row["outside_distribution"] is True
    assert "OUTSIDE" in row["note"]
    assert row["percentile"] == pytest.approx(100.0)


def test_a_realized_return_at_the_fold_mean_is_inside_the_band(user):
    strategy = _strategy(user)
    # Flat prices ⇒ realized 0%; the fold's mean daily return is 0 too.
    _bars("AAPL", START, [100] * 60)
    _cycles(strategy, 6)
    _backtest(strategy, sigma=0.01, mean_daily=0.0)

    out = evr.expected_vs_realized(strategy, end_date=START + dt.timedelta(days=45))
    assert out["provisional"] is False, out["provisional_reasons"]
    assert all(r["realized_return_pct"] == pytest.approx(0.0, abs=1e-6) for r in out["cycles"])
    assert all(abs(r["z_score"]) < evr.Z_OUTSIDE for r in out["cycles"])
    assert all(r["outside_distribution"] is False for r in out["cycles"])
    assert out["summary"]["inside_ratio"] == 1.0
    assert out["summary"]["outside_ratio"] == 0.0
    assert out["summary"]["mean_gap_pp"] is not None


def test_percentile_is_empirical_when_the_fold_has_enough_windows(user):
    strategy = _strategy(user)
    _bars("AAPL", START, [100] * 60)
    _cycles(strategy, 6)
    _backtest(strategy, oos_days=63)

    out = evr.expected_vs_realized(strategy, end_date=START + dt.timedelta(days=45))
    assert {r["percentile_method"] for r in out["cycles"]} == {"empirical"}
    assert all(0.0 <= r["percentile"] <= 100.0 for r in out["cycles"])
    assert all(r["sample_size"] > 0 for r in out["cycles"])


def test_fold_without_a_daily_series_pro_rates_its_total_and_gives_no_z(user):
    strategy = _strategy(user)
    _bars("AAPL", START, [100] * 60)
    _cycles(strategy, 6)
    _backtest(strategy, daily_series=False)

    out = evr.expected_vs_realized(strategy, end_date=START + dt.timedelta(days=45))
    for row in out["cycles"]:
        assert row["expected_source"] == "fold_total"
        assert row["expected_return_pct"] is not None
        assert row["z_score"] is None
        assert row["percentile"] is None
        assert row["outside_distribution"] is False
        assert "no distribution" in row["note"]
    # Scored (there IS an expectation) but never counted as inside/outside.
    assert out["summary"]["scored"] == 6
    assert out["summary"]["z_scored"] == 0
    assert out["summary"]["inside_ratio"] is None


def test_sessions_assumed_scales_the_expectation_with_the_holding_interval(user):
    strategy = _strategy(user)
    _bars("AAPL", START, [100] * 90)
    _cycles(strategy, 2, step_days=28)
    _backtest(strategy, mean_daily=0.001, sigma=0.0001)

    out = evr.expected_vs_realized(strategy, end_date=START + dt.timedelta(days=56))
    first = out["cycles"][0]
    assert first["period_days"] == 28
    assert first["sessions_assumed"] == evr.sessions_in(28) == 19
    # ~19 sessions of a +0.1%/day drift compounds to roughly +1.9%.
    assert first["expected_return_pct"] == pytest.approx(1.92, abs=0.15)


# ---------------------------------------------------------------------------
# Provisional contract + endpoint
# ---------------------------------------------------------------------------
def test_fewer_than_min_scored_cycles_is_provisional_with_null_ratios(user):
    strategy = _strategy(user)
    _bars("AAPL", START, [100] * 40)
    _cycles(strategy, evr.MIN_SCORED_CYCLES - 1)
    _backtest(strategy)

    out = evr.expected_vs_realized(strategy, end_date=START + dt.timedelta(days=35))
    assert out["summary"]["scored"] == evr.MIN_SCORED_CYCLES - 1
    assert out["provisional"] is True
    assert any("scored cycle(s)" in r for r in out["provisional_reasons"])
    assert out["summary"]["inside_ratio"] is None
    assert out["summary"]["mean_expected_pct"] is None
    assert out["summary"]["mean_gap_pp"] is None
    # The rows themselves keep every number they honestly have.
    assert all(r["expected_return_pct"] is not None for r in out["cycles"])


def test_endpoint_serves_the_payload_and_is_owner_scoped(client, user, db):
    strategy = _strategy(user)
    _bars("AAPL", START, [100] * 60)
    _cycles(strategy, 6)
    _backtest(strategy)

    resp = client.get(f"/api/strategies/{strategy.id}/expected-vs-realized/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["strategy_id"] == strategy.id
    assert set(body) >= {
        "strategy_id", "strategy_name", "end_date", "provisional", "provisional_reasons",
        "backtest", "cycles", "summary",
    }
    assert body["summary"]["z_outside_threshold"] == evr.Z_OUTSIDE

    other = User.objects.create_user(email="w3p3-other@x.test", password="pw-fake-123456789")
    intruder = APIClient()
    intruder.force_authenticate(other)
    assert intruder.get(
        f"/api/strategies/{strategy.id}/expected-vs-realized/"
    ).status_code == 404


def test_realized_returns_come_from_the_shared_disjoint_period_helper(user, monkeypatch):
    """WP-P1's ``period_returns`` is imported, not reimplemented — patching it
    changes this module's realized numbers."""
    strategy = _strategy(user)
    _bars("AAPL", START, [100] * 40)
    _cycles(strategy, 3)

    called: list[dict] = []
    real = evr.period_returns

    def _spy(targets, **kw):
        called.append(kw)
        return real(targets, **kw)

    monkeypatch.setattr(evr, "period_returns", _spy)
    evr.expected_vs_realized(strategy, end_date=START + dt.timedelta(days=25))
    assert called and called[0]["end_date"] == START + dt.timedelta(days=25)
