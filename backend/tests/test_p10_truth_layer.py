"""P10 §B — the truth layer.

B1: baseline_curve is total-return (adjusted_close) + TRUE equal-weight
    (chained per-ticker returns), with a recompute command that preserves the
    legacy value. B2: SPY-TR/QQQ-TR beta / CAPM alpha / IR + rolling Sharpe.
B3: §9 gate requires stitched OOS Sharpe > 0 and excludes synthetic +
    price-only-era rows. B4: deflation suppressed where it guards nothing;
    news_sentiment / pairs kinds rejected by the backtest serializer.
B5: fund composite endpoint (pods' stitched OOS curves vs SPY/QQQ).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.backtests.metrics import (
    baseline_curve,
    benchmark_curve,
    benchmark_stats,
    beta_alpha_ir,
    rolling_sharpe_series,
)
from apps.backtests.models import Backtest, BacktestDay, BacktestFold, BacktestMetrics
from apps.backtests.serializers import BacktestCreateSerializer
from apps.data.models import DailyBar
from apps.portfolios.models import (
    AutonomousFund,
    Portfolio,
    PortfolioStrategy,
    Universe,
    UniverseMembership,
)
from apps.portfolios.validation import validation_status

User = get_user_model()

D0 = dt.date(2024, 1, 1)


@pytest.fixture
def user(db):
    return User.objects.create_user(email="p10b@x.test", password="pw-fake-123456789")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _bar(ticker, date, close, adj=None):
    DailyBar.objects.create(
        ticker=ticker, date=date, open=close, high=close, low=close,
        close=Decimal(str(close)),
        adjusted_close=Decimal(str(adj if adj is not None else close)),
        volume=1000, source="test",
    )


def _dates(n, start=D0):
    return [start + dt.timedelta(days=i) for i in range(n)]


def _backtest(user, *, universe=None, baseline="universe_ew", **kw):
    return Backtest.objects.create(
        user=user, name="bt", universe=universe or ["AAA", "BBB"],
        start_date=D0, end_date=D0 + dt.timedelta(days=400),
        baseline=baseline, **kw,
    )


def _strategy(user, name="S", kind=PortfolioStrategy.KIND_RISK_PARITY):
    u = Universe.objects.create(name=f"p10-uni-{name}")
    UniverseMembership.objects.create(universe=u, ticker="AAA", effective_from=D0)
    pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_STRATEGY, name=name)
    return PortfolioStrategy.objects.create(
        user=user, name=name, universe=u, portfolio=pf, kind=kind,
    )


# ---------------------------------------------------------------------------
# B1 — baseline_curve: total-return + true equal weight.
# ---------------------------------------------------------------------------
def test_baseline_uses_adjusted_close(db, user):
    """A flat close with a rising adjusted_close (dividends) must produce a
    rising baseline — the old close-only code returned a flat line."""
    days = _dates(3)
    for i, d in enumerate(days):
        _bar("AAA", d, 100, adj=100 * (1.01 ** i))
    bt = _backtest(user, universe=["AAA"])
    curve = baseline_curve(bt, days)
    assert curve[-1] > curve[0]
    assert curve[-1] / curve[0] == pytest.approx(1.01 ** 2, rel=1e-9)


def test_baseline_is_equal_weight_not_price_weighted(db, user):
    """A $1000 ticker that doubles and a $1 ticker that halves: TRUE equal
    weight compounds (1 + (+100% −50%)/2) = +25%/day; the old price-level
    average was dominated by the expensive ticker (~+100%)."""
    days = _dates(2)
    _bar("EXP", days[0], 1000)
    _bar("EXP", days[1], 2000)
    _bar("CHP", days[0], 1)
    _bar("CHP", days[1], 0.5)
    bt = _backtest(user, universe=["EXP", "CHP"])
    curve = baseline_curve(bt, days)
    assert curve[1] / curve[0] == pytest.approx(1.25, rel=1e-9)


def test_baseline_missing_bar_carries(db, user):
    """A ticker missing a bar contributes nothing that day (carry), and its
    return resumes from its own last price — no liquidation artifacts."""
    days = _dates(3)
    for i, d in enumerate(days):
        _bar("AAA", d, 100 + i)  # +1%/0.99% steps
    _bar("BBB", days[0], 50)
    _bar("BBB", days[2], 55)     # missing on day 1
    bt = _backtest(user, universe=["AAA", "BBB"])
    curve = baseline_curve(bt, days)
    # Day1: only AAA (+1%); Day2: mean(AAA +0.990%, BBB +10%) ≈ +5.495%.
    assert curve[1] / curve[0] == pytest.approx(1.01, rel=1e-9)
    expected_d2 = 1 + ((101 / 100 - 1) + (55 / 50 - 1)) / 2 - 1 + 1  # clarity below
    assert curve[2] / curve[1] == pytest.approx(
        1 + ((102 / 101 - 1) + (55 / 50 - 1)) / 2, rel=1e-9,
    )
    assert expected_d2  # silence lint on the intermediate


def _make_done_backtest_with_days(user, *, universe, n_days=10, daily=0.0, rets=None):
    """A DONE backtest with one fold of OOS BacktestDays (constant ``daily``
    return, or an explicit per-step ``rets`` pattern), plus a metrics row."""
    days = _dates(n_days)
    bt = _backtest(user, universe=universe, status=Backtest.DONE)
    fold = BacktestFold.objects.create(
        backtest=bt, fold_index=0, is_start=days[0], is_end=days[0],
        oos_start=days[0], oos_end=days[-1],
        is_sharpe=Decimal("1.0"), oos_sharpe=Decimal("1.0"),
    )
    v = 100000.0
    for i, d in enumerate(days):
        if i:
            v *= 1 + (rets[(i - 1) % len(rets)] if rets else daily)
        BacktestDay.objects.create(
            backtest=bt, fold=fold, segment=BacktestDay.SEG_OOS, date=d,
            cash=Decimal("0"), positions=[], portfolio_value=Decimal(str(round(v, 2))),
        )
    metrics = BacktestMetrics.objects.create(
        backtest=bt, baseline_return_pct=Decimal("246.0"),
    )
    return bt, metrics, days


# ---------------------------------------------------------------------------
# B1 — recompute_baselines command: rewrite + preserve-once.
# ---------------------------------------------------------------------------
def test_recompute_baselines_preserves_legacy_once(db, user):
    bt, metrics, days = _make_done_backtest_with_days(user, universe=["AAA"])
    for i, d in enumerate(days):
        _bar("AAA", d, 100, adj=100 * (1.02 ** i))
    out = StringIO()
    call_command("recompute_baselines", ids=[bt.id], stdout=out)
    metrics.refresh_from_db()
    expected = (1.02 ** (len(days) - 1) - 1) * 100
    assert float(metrics.baseline_return_pct) == pytest.approx(expected, abs=1e-3)
    assert metrics.baseline_return_pct_legacy == Decimal("246.0")
    # Second run must keep the ORIGINAL legacy value.
    call_command("recompute_baselines", ids=[bt.id], stdout=StringIO())
    metrics.refresh_from_db()
    assert metrics.baseline_return_pct_legacy == Decimal("246.0")


# ---------------------------------------------------------------------------
# B2 — benchmark stats: beta / CAPM alpha / IR.
# ---------------------------------------------------------------------------
def test_beta_alpha_ir_identity():
    rets = [0.01, -0.005, 0.007, -0.002, 0.004] * 4
    out = beta_alpha_ir(rets, rets)
    assert out["beta"] == pytest.approx(1.0, abs=1e-9)
    assert out["alpha_annual_pct"] == pytest.approx(0.0, abs=1e-9)
    assert out["information_ratio"] == pytest.approx(0.0, abs=1e-9)


def test_beta_alpha_ir_leveraged():
    bench = [0.01, -0.005, 0.007, -0.002, 0.004] * 4
    strat = [2 * r for r in bench]
    out = beta_alpha_ir(strat, bench)
    assert out["beta"] == pytest.approx(2.0, abs=1e-9)
    assert out["alpha_annual_pct"] == pytest.approx(0.0, abs=1e-9)


def test_beta_alpha_ir_survives_one_day_timestamp_offset():
    """Engine-stored OOS curves lag benchmark bars by ~one session (mark-to-prior-
    close), which zeroes a naive contemporaneous daily beta. The block-compounded
    estimator must still recover most of the true exposure on a long series."""
    import random

    rng = random.Random(42)
    bench = [rng.gauss(0.0004, 0.012) for _ in range(800)]
    true_beta = 0.5
    strat_aligned = [true_beta * r + rng.gauss(0.0, 0.002) for r in bench]
    # Shift the strategy one step late vs the benchmark (the engine's offset).
    strat_lagged = [0.0] + strat_aligned[:-1]

    naive_window = 50  # below the blocking threshold → contemporaneous daily
    naive = beta_alpha_ir(strat_lagged[:naive_window], bench[:naive_window])
    blocked = beta_alpha_ir(strat_lagged, bench)

    assert abs(naive["beta"]) < 0.2          # the failure mode: beta collapses
    assert blocked["beta"] > 0.3             # blocked estimator recovers exposure
    assert blocked["beta"] == pytest.approx(true_beta * 0.8, rel=0.35)


def test_benchmark_stats_block(db, user):
    # A VARIED return pattern (constant returns leave the benchmark with ~zero
    # variance, making beta undefined); strategy ≡ benchmark → beta 1, alpha 0.
    pattern = [0.012, -0.008, 0.02, -0.004, 0.007, -0.011, 0.015, 0.003, -0.006]
    bt, _, days = _make_done_backtest_with_days(
        user, universe=["AAA"], n_days=10, rets=pattern,
    )
    px = 100.0
    _bar("SPY", days[0], px)
    for i in range(1, len(days)):
        px *= 1 + pattern[(i - 1) % len(pattern)]
        _bar("SPY", days[i], px)
    from apps.backtests.metrics import stitched_oos_returns

    dates, equity, _ = stitched_oos_returns(bt)
    out = benchmark_stats(bt, dates, equity)
    assert "SPY" in out and "QQQ" not in out          # no QQQ bars → omitted
    spy = out["SPY"]
    expected_total = 1.0
    for r in pattern:
        expected_total *= 1 + r
    assert spy["total_return_pct"] == pytest.approx((expected_total - 1) * 100, abs=1e-2)
    assert spy["beta"] == pytest.approx(1.0, abs=1e-2)  # identical return stream
    assert spy["alpha_annual_pct"] == pytest.approx(0.0, abs=0.5)


def test_rolling_sharpe_series_sampling():
    days = _dates(300)
    equity = [100 * (1.001 ** i) for i in range(300)]
    out = rolling_sharpe_series(days, equity, window=100, step=50)
    assert len(out) == (299 - 100) // 50 + 1
    assert all(p["sharpe"] == 0.0 or p["sharpe"] > 0 for p in out)


def test_benchmark_curve_no_bars_returns_empty(db, user):
    assert benchmark_curve("SPY", _dates(5), 100.0) == []


# ---------------------------------------------------------------------------
# B3 — §9 gate hardening.
# ---------------------------------------------------------------------------
def _gate_backtest(strategy, *, status=Backtest.DONE, era=Backtest.ERA_TOTAL_RETURN,
                   stitched="0.7", oos="0.9", dd="4.0"):
    bt = Backtest.objects.create(
        user=strategy.user, strategy=strategy, name="gate-bt",
        start_date=dt.date(2024, 1, 1), end_date=dt.date(2025, 1, 1),
        status=status, data_era=era,
    )
    BacktestMetrics.objects.create(
        backtest=bt, mean_oos_sharpe=Decimal(oos), sharpe=Decimal(stitched),
        max_drawdown_pct=Decimal(dd),
    )
    return bt


def test_gate_requires_positive_stitched_sharpe(db, user):
    s = _strategy(user)
    _gate_backtest(s, stitched="-0.1", oos="1.2")
    out = validation_status(s)
    assert out["passed"] is False
    assert any(
        c["key"] == "positive_stitched_sharpe" and not c["ok"] for c in out["checks"]
    )


def test_gate_excludes_synthetic_rows(db, user):
    s = _strategy(user)
    _gate_backtest(s, status=Backtest.SYNTHETIC)
    out = validation_status(s)
    assert out["passed"] is False
    assert out["backtest_id"] is None


def test_gate_excludes_price_only_era(db, user):
    s = _strategy(user)
    _gate_backtest(s, era=Backtest.ERA_PRICE_ONLY)
    out = validation_status(s)
    assert out["passed"] is False
    assert out["backtest_id"] is None


def test_gate_passes_on_real_tr_record(db, user):
    s = _strategy(user)
    _gate_backtest(s)
    assert validation_status(s)["passed"] is True


# ---------------------------------------------------------------------------
# B4 — deflation suppression + kind rejection.
# ---------------------------------------------------------------------------
def test_deflation_meaningful_union_rule(db, user):
    det = _backtest(user, engine_mode=Backtest.RISK_PARITY, n_candidates=50)
    assert det.deflation_meaningful is False           # engine-mode leg
    legacy = _backtest(user, engine_mode="market_neutral", n_candidates=1)
    assert legacy.deflation_meaningful is False        # n_candidates leg
    council = _backtest(user, engine_mode=Backtest.COUNCIL, n_candidates=50)
    assert council.deflation_meaningful is True


def test_create_serializer_forces_n_candidates_for_deterministic(db, user):
    req = SimpleNamespace(user=user)
    ser = BacktestCreateSerializer(
        data={
            "name": "det", "universe": ["AAA"],
            "start_date": "2023-01-02", "end_date": "2025-12-31",
            "engine_mode": "risk_parity", "n_candidates": 50,
        },
        context={"request": req},
    )
    assert ser.is_valid(), ser.errors
    assert ser.validated_data["n_candidates"] == 1


@pytest.mark.parametrize("kind,frag", [
    (PortfolioStrategy.KIND_NEWS_SENTIMENT, "can't be backtested"),
    (PortfolioStrategy.KIND_PAIRS, "no backtest engine mode"),
])
def test_create_serializer_rejects_unbacktestable_kinds(db, user, kind, frag):
    s = _strategy(user, name=f"k-{kind}", kind=kind)
    req = SimpleNamespace(user=user)
    ser = BacktestCreateSerializer(
        data={
            "name": "x", "universe": ["AAA"],
            "start_date": "2023-01-02", "end_date": "2025-12-31",
            "strategy_id": s.id,
        },
        context={"request": req},
    )
    assert not ser.is_valid()
    assert frag in str(ser.errors["strategy_id"])


def test_deflation_view_flags_meaningless(db, client, user):
    bt = _backtest(user, engine_mode=Backtest.RISK_PARITY, status=Backtest.DONE)
    BacktestMetrics.objects.create(backtest=bt, sharpe_deflation=Decimal("0.1"))
    r = client.get(f"/api/backtests/{bt.id}/deflation/")
    assert r.status_code == 200
    body = r.json()
    assert body["deflation_meaningful"] is False
    assert body["red_flag"] is False                   # suppressed, not scary


# ---------------------------------------------------------------------------
# B2 — equity-curve endpoint: benchmark overlays + rolling sharpe present.
# ---------------------------------------------------------------------------
def test_equity_curve_includes_benchmarks(db, client, user):
    bt, _, days = _make_done_backtest_with_days(
        user, universe=["AAA"], n_days=5, daily=0.01,
    )
    for i, d in enumerate(days):
        _bar("SPY", d, 500 * (1.005 ** i))
    r = client.get(f"/api/backtests/{bt.id}/equity-curve/")
    assert r.status_code == 200
    body = r.json()
    assert body["benchmarks"] == ["SPY"]
    assert "rolling_sharpe" in body
    assert body["points"][0]["spy"] == pytest.approx(100000.0, rel=1e-6)
    assert body["points"][-1]["spy"] == pytest.approx(100000.0 * 1.005 ** 4, rel=1e-6)


# ---------------------------------------------------------------------------
# B5 — fund composite endpoint.
# ---------------------------------------------------------------------------
def test_fund_composite_endpoint(db, client, user):
    fund = AutonomousFund.objects.create(owner=user, name="Fund")
    days = _dates(6)
    for i, d in enumerate(days):
        _bar("SPY", d, 100 * (1.002 ** i))
    rates = [0.01, 0.02]
    for j, daily in enumerate(rates):
        s = _strategy(user, name=f"pod{j}")
        fund.strategies.add(s)
        bt, _, _ = _make_done_backtest_with_days(
            user, universe=["AAA"], n_days=6, daily=daily,
        )
        bt.strategy = s
        bt.save(update_fields=["strategy"])
    r = client.get("/api/fund/composite/")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert len(body["members"]) == 2
    assert all(m["weight"] == 0.5 for m in body["members"])
    pts = body["points"]
    # Equal-weight composite of +1%/d and +2%/d books, normalized to 100.
    expected_last = 100 * (1.01 ** 5 + 1.02 ** 5) / 2
    assert pts[-1]["composite"] == pytest.approx(expected_last, rel=1e-4)
    assert pts[-1]["spy"] == pytest.approx(100 * 1.002 ** 5, rel=1e-4)
    assert "composite" in body["metrics"] and "SPY" in body["metrics"]
    assert "vs_spy" in body["metrics"]["composite"]


def test_fund_composite_custom_weights(db, client, user):
    fund = AutonomousFund.objects.create(owner=user, name="Fund")
    sids = []
    for j, daily in enumerate([0.01, 0.02]):
        s = _strategy(user, name=f"podw{j}")
        fund.strategies.add(s)
        bt, _, _ = _make_done_backtest_with_days(
            user, universe=["AAA"], n_days=6, daily=daily,
        )
        bt.strategy = s
        bt.save(update_fields=["strategy"])
        sids.append(s.id)
    r = client.get(f"/api/fund/composite/?weights={sids[0]}:0.6,{sids[1]}:0.4")
    body = r.json()
    expected_last = 100 * (0.6 * 1.01 ** 5 + 0.4 * 1.02 ** 5)
    assert body["points"][-1]["composite"] == pytest.approx(expected_last, rel=1e-4)


def test_fund_composite_excludes_price_only_records(db, client, user):
    fund = AutonomousFund.objects.create(owner=user, name="Fund")
    s = _strategy(user, name="pold")
    fund.strategies.add(s)
    bt, _, _ = _make_done_backtest_with_days(user, universe=["AAA"], n_days=6)
    bt.strategy = s
    bt.data_era = Backtest.ERA_PRICE_ONLY
    bt.save(update_fields=["strategy", "data_era"])
    r = client.get("/api/fund/composite/")
    body = r.json()
    assert body["available"] is False
    assert s.name in body["missing"]
