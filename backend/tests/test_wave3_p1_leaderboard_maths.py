"""Wave 3 / WP P1 — leaderboard maths that the review's proof tests do not cover.

Disjoint per-period returns, cadence-based annualisation, the bounded Sortino,
provisional-last ordering, per-model PnL attribution, the ``recompute_leaderboard``
management command, and the stale-row guard.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.backtests.models import Backtest, BacktestMetrics
from apps.data.models import DailyBar
from apps.leaderboard import compute, period_returns
from apps.leaderboard.models import METRICS_VERSION, AgentScorecard, StrategyScorecard
from apps.portfolios.models import Portfolio, PortfolioStrategy, PortfolioTarget, Universe
from apps.runs.models import AgentMessage, Run

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="w3p1@x.test", password="pw-fake-123456789")


def _bars(ticker, start: dt.date, closes, step_days: int = 1):
    for i, c in enumerate(closes):
        DailyBar.objects.create(
            ticker=ticker, date=start + dt.timedelta(days=i * step_days),
            open=c, high=c, low=c, close=c, adjusted_close=c, volume=1000, source="fmp",
        )


def _strategy(user, name="w3", kind=PortfolioStrategy.KIND_LONG_ONLY, cash="100000"):
    universe = Universe.objects.create(name=f"uni-{name}", is_active=True)
    portfolio = Portfolio.objects.create(
        user=user, name=f"{name}-book", kind="strategy", cash_balance=Decimal(cash),
    )
    return PortfolioStrategy.objects.create(
        user=user, name=name, kind=kind, universe=universe, portfolio=portfolio,
    )


def _cycles(strategy, start: dt.date, n: int, weights, step_days: int = 1):
    for i in range(n):
        PortfolioTarget.objects.create(
            strategy=strategy, as_of_date=start + dt.timedelta(days=i * step_days),
            status=PortfolioTarget.DONE, target_weights=weights,
        )


# ---------------------------------------------------------------------------
# period_returns — the disjoint series itself
# ---------------------------------------------------------------------------


def test_period_returns_are_disjoint_and_telescope(db):
    start = dt.date(2026, 3, 2)
    _bars("AAPL", start, [100, 110, 121, 133.1])
    targets = [
        SimpleNamespace(as_of_date=start + dt.timedelta(days=i), target_weights={"AAPL": 1.0})
        for i in range(3)
    ]
    periods = period_returns.period_returns(targets, end_date=start + dt.timedelta(days=3))
    assert [p.start for p in periods] == [
        start, start + dt.timedelta(days=1), start + dt.timedelta(days=2),
    ]
    # Each interval ends where the next begins — no shared days.
    assert [p.end for p in periods][:-1] == [p.start for p in periods][1:]
    assert all(p.ret == pytest.approx(0.10) for p in periods)
    chained = 1.0
    for p in periods:
        chained *= 1 + p.ret
    assert chained - 1 == pytest.approx(133.1 / 100 - 1)  # telescopes to the real move


def test_period_returns_respect_signed_weights(db):
    start = dt.date(2026, 3, 2)
    _bars("AAPL", start, [100, 110])
    _bars("MSFT", start, [50, 45])
    targets = [SimpleNamespace(as_of_date=start, target_weights={"AAPL": 0.5, "MSFT": -0.5})]
    (period,) = period_returns.period_returns(
        targets, end_date=start + dt.timedelta(days=1)
    )
    # 0.5 * +10% + (-0.5) * -10% = +10%
    assert period.ret == pytest.approx(0.10)


def test_unpriceable_interval_is_dropped_not_zeroed(db):
    start = dt.date(2026, 3, 2)
    targets = [SimpleNamespace(as_of_date=start, target_weights={"NOBARS": 1.0})]
    assert period_returns.period_returns(
        targets, end_date=start + dt.timedelta(days=1)
    ) == []


def test_repeated_as_of_date_is_one_holding_period(db):
    start = dt.date(2026, 3, 2)
    _bars("AAPL", start, [100, 101])
    targets = [
        SimpleNamespace(as_of_date=start, target_weights={"AAPL": 1.0}),
        SimpleNamespace(as_of_date=start, target_weights={"AAPL": 1.0}),  # re-issue
    ]
    periods = period_returns.period_returns(
        targets, end_date=start + dt.timedelta(days=1)
    )
    assert len(periods) == 1


def test_periods_per_year_follows_observed_cadence():
    def _p(days):
        return period_returns.Period(
            start=dt.date(2026, 1, 1), end=dt.date(2026, 1, 1), ret=0.0, days=days,
        )

    assert period_returns.periods_per_year([_p(1)] * 5) == pytest.approx(252.0)  # clamped
    assert period_returns.periods_per_year([_p(7)] * 5) == pytest.approx(52.18, abs=0.1)
    assert period_returns.periods_per_year([_p(30)] * 5) == pytest.approx(12.18, abs=0.1)
    assert period_returns.periods_per_year([]) is None


# ---------------------------------------------------------------------------
# bounded Sortino
# ---------------------------------------------------------------------------


def test_sortino_is_null_without_enough_downside():
    rets = [0.01] * 12 + [-0.001]  # one negative period out of 13
    value, note = compute._sortino(rets, 252.0)
    assert value is None
    assert "negative periods" in note


def test_sortino_is_null_with_zero_downside():
    value, note = compute._sortino([0.01, 0.02, 0.03], 252.0)
    assert value is None and note


def test_sortino_is_capped_not_astronomical():
    # 3 barely-negative periods among many strong positives: the denominator is
    # tiny and the raw ratio explodes (this is the shape that produced 911.38).
    rets = [0.05] * 40 + [-1e-6] * 3
    value, note = compute._sortino(rets, 252.0)
    assert value == pytest.approx(compute.SORTINO_CAP)
    assert "capped" in note
    assert abs(value) <= compute.SORTINO_CAP


def test_sharpe_is_bounded():
    assert compute._sharpe([0.05] * 30, 252.0) is None  # zero dispersion
    value = compute._sharpe([0.05, 0.0500001] * 20, 252.0)
    assert abs(value) <= compute.SHARPE_CAP


# ---------------------------------------------------------------------------
# provisional handling + ordering
# ---------------------------------------------------------------------------


def test_provisional_row_keeps_facts_and_drops_ratios(user):
    today = timezone.localdate()
    base = today - dt.timedelta(days=5)
    _bars("AAPL", base, [100, 102, 101, 103, 104, 105])
    s = _strategy(user, name="small")
    _cycles(s, base, 5, {"AAPL": 1.0})
    compute.recompute_strategies(today)
    sc = StrategyScorecard.objects.get(strategy=s, window="lifetime", as_of=today)
    assert sc.provisional is True
    assert sc.sharpe is None and sc.sortino is None and sc.annualised_return_pct is None
    # …but the honest small-sample facts survive.
    assert sc.n_cycles == 5 and sc.n_observations == 5
    assert sc.total_return_pct is not None
    assert sc.hit_rate is not None
    assert sc.max_drawdown_pct is not None
    assert "fewer than 20 observations" in sc.sortino_note


def test_provisional_rows_sort_below_real_ones(user):
    today = timezone.localdate()
    base = today - dt.timedelta(days=30)
    # A real 25-observation book that made a modest +~3%.
    closes = [100.0]
    for i in range(25):
        closes.append(round(closes[-1] * (1.004 if i % 3 else 0.997), 4))
    _bars("REAL", base, closes)
    real = _strategy(user, name="real")
    _cycles(real, base, 25, {"REAL": 1.0})
    # A 3-cycle book that "made" +50% — it would top a total_return_pct sort.
    _bars("TINY", base, [100, 120, 140, 150])
    tiny = _strategy(user, name="tiny")
    _cycles(tiny, base, 3, {"TINY": 1.0})

    compute.recompute_strategies(today)
    client = APIClient()
    client.force_authenticate(user)
    rows = client.get(
        "/api/leaderboard/strategies/?window=lifetime&sort=total_return_pct"
    ).json()["rows"]
    assert [r["strategy_name"] for r in rows] == ["real", "tiny"]
    assert rows[0]["provisional"] is False
    assert rows[1]["provisional"] is True and rows[1]["total_return_pct"] is not None


# ---------------------------------------------------------------------------
# agent PnL attribution is per-model, not a per-strategy constant
# ---------------------------------------------------------------------------


def test_agent_pnl_is_attributed_per_model(user):
    """Production showed lynch = −29.69 bps on four different model rows: the
    same backtest attribution number copied onto every model the persona had
    ever run under. Attribution now matches the model the backtest actually
    used, and rows with no matching backtest get null rather than a loan."""
    bt = Backtest.objects.create(
        user=user, name="bt", universe=["AAPL"],
        start_date=dt.date(2026, 1, 1), end_date=dt.date(2026, 2, 1),
        starting_cash=Decimal("100000"), status=Backtest.DONE,
        model_overrides={"lynch": "m1"},
    )
    BacktestMetrics.objects.create(backtest=bt, per_agent_attribution={"lynch": -296.9})

    assert compute._agent_pnl_bps("lynch", "m1") == pytest.approx(-29.69)
    assert compute._agent_pnl_bps("lynch", "m2") is None
    assert compute._agent_pnl_bps("lynch", "") is None


def test_agent_scorecard_rows_do_not_share_a_pnl_number(user):
    as_of = dt.date(2026, 5, 1)
    _bars("AAPL", as_of, [100, 101, 102, 103, 104, 105])
    _bars("MSFT", as_of, [100, 101, 102, 103, 104, 105])
    bt = Backtest.objects.create(
        user=user, name="bt", universe=["AAPL"],
        start_date=dt.date(2026, 1, 1), end_date=dt.date(2026, 2, 1),
        starting_cash=Decimal("100000"), status=Backtest.DONE,
        model_overrides={"lynch": "m1"},
    )
    BacktestMetrics.objects.create(backtest=bt, per_agent_attribution={"lynch": -296.9})
    for ticker, model in (("AAPL", "m1"), ("MSFT", "m2")):
        run = Run.objects.create(
            user=user, tickers=[ticker], as_of_date=as_of, status=Run.DONE,
            model_overrides={"lynch": model}, agent_versions={"lynch": "v1"},
        )
        AgentMessage.objects.create(
            run=run, agent_name="lynch",
            parsed_output={"signal": "bullish", "confidence": 60},
        )
    today = timezone.localdate()
    compute.recompute_agents(today)
    m1 = AgentScorecard.objects.get(agent_name="lynch", model_id="m1", window="lifetime")
    m2 = AgentScorecard.objects.get(agent_name="lynch", model_id="m2", window="lifetime")
    assert float(m1.pnl_contribution_bps) == pytest.approx(-29.69)
    assert m2.pnl_contribution_bps is None


# ---------------------------------------------------------------------------
# recompute path: management command + stale-row guard
# ---------------------------------------------------------------------------


def test_recompute_leaderboard_command_is_idempotent(user, capsys):
    today = timezone.localdate()
    base = today - dt.timedelta(days=6)
    _bars("AAPL", base, [100, 101, 102, 103, 104, 105, 106])
    s = _strategy(user, name="cmd")
    _cycles(s, base, 6, {"AAPL": 1.0})

    call_command("recompute_leaderboard")
    first = list(
        StrategyScorecard.objects.filter(as_of=today)
        .order_by("id").values_list("window", "n_observations", "total_return_pct")
    )
    call_command("recompute_leaderboard")
    second = list(
        StrategyScorecard.objects.filter(as_of=today)
        .order_by("id").values_list("window", "n_observations", "total_return_pct")
    )
    assert first == second
    assert StrategyScorecard.objects.filter(
        as_of=today, metrics_version=METRICS_VERSION
    ).count() == StrategyScorecard.objects.filter(as_of=today).count()

    # --check passes once everything is on the current metrics version…
    call_command("recompute_leaderboard", "--check")
    # …and fails while a stale row exists.
    StrategyScorecard.objects.filter(strategy=s).update(metrics_version=0)
    with pytest.raises(CommandError):
        call_command("recompute_leaderboard", "--check")


def test_recompute_leaderboard_command_accepts_as_of(user):
    as_of = timezone.localdate() - dt.timedelta(days=2)
    _strategy(user, name="asof")
    call_command("recompute_leaderboard", "--as-of", as_of.isoformat())
    assert StrategyScorecard.objects.filter(as_of=as_of).exists()
    with pytest.raises(CommandError):
        call_command("recompute_leaderboard", "--as-of", "not-a-date")


def test_stale_rows_never_serve_a_ratio(user):
    """A row written by the old maths cannot be read as a Sharpe even before
    the recompute has run (the 0006 migration nulls them; this is the API-side
    guard for anything that survives)."""
    today = timezone.localdate()
    StrategyScorecard.objects.create(
        strategy=None, user=user, flavor=PortfolioStrategy.KIND_LONG_ONLY,
        window="lifetime", as_of=today, n_cycles=13,
        total_return_pct=Decimal("2.08"), sharpe=Decimal("7.2400"),
        sortino=Decimal("911.3800"), annualised_return_pct=Decimal("14000.0000"),
        hit_rate=Decimal("0.2308"), provisional=False, metrics_version=0,
    )
    client = APIClient()
    client.force_authenticate(user)
    (row,) = client.get(
        "/api/leaderboard/strategies/by-flavor/?window=lifetime"
    ).json()["rows"]
    assert row["sharpe"] is None
    assert row["sortino"] is None
    assert row["annualised_return_pct"] is None
    assert row["provisional"] is True
    assert "stale" in row["sortino_note"]
    # The small-sample facts are still shown.
    assert row["n_cycles"] == 13
    assert float(row["total_return_pct"]) == 2.08
    assert float(row["hit_rate"]) == 0.2308


def test_recompute_all_reports_the_metrics_version(user):
    out = compute.recompute_all(timezone.localdate())
    assert out["metrics_version"] == METRICS_VERSION


def test_data_migration_marks_pre_fix_rows_stale(user):
    """0006 backfills the owner on per-strategy rows, strips the ratios the old
    maths got wrong, and drops the ownerless global aggregates."""
    import importlib

    from django.apps import apps as django_apps

    migration = importlib.import_module(
        "apps.leaderboard.migrations.0006_mark_pre_v2_scorecards_stale"
    )
    today = timezone.localdate()
    s = _strategy(user, name="legacy")
    per_strategy = StrategyScorecard.objects.create(
        strategy=s, user=None, flavor=s.kind, window="lifetime", as_of=today,
        n_cycles=13, total_return_pct=Decimal("2.08"), sharpe=Decimal("7.2400"),
        sortino=Decimal("911.3800"), annualised_return_pct=Decimal("14000.0000"),
        hit_rate=Decimal("0.2308"), provisional=False, metrics_version=0,
    )
    global_flavor = StrategyScorecard.objects.create(
        strategy=None, user=None, flavor=s.kind, window="lifetime", as_of=today,
        n_cycles=4, sharpe=Decimal("3.0000"), metrics_version=0,
    )
    global_agent = AgentScorecard.objects.create(
        agent_name="lynch", window="lifetime", as_of=today, n_decisions=10,
        n_directional=10, hit_rate=Decimal("0.9000"), metrics_version=0,
    )

    migration.mark_stale(django_apps, None)

    assert not StrategyScorecard.objects.filter(pk=global_flavor.pk).exists()
    assert not AgentScorecard.objects.filter(pk=global_agent.pk).exists()
    per_strategy.refresh_from_db()
    assert per_strategy.user_id == user.id
    assert per_strategy.sharpe is None
    assert per_strategy.sortino is None
    assert per_strategy.annualised_return_pct is None
    assert per_strategy.metrics_version == 0  # still stale until the recompute
    # The small-sample facts are untouched.
    assert float(per_strategy.total_return_pct) == 2.08
    assert float(per_strategy.hit_rate) == 0.2308
