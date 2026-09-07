"""Adversarial review (reviewer: backtests) — §9 validation-gate proofs.

Each test documents a concrete way the autopilot gate could be satisfied by a
backtest that does not model the strategy it is supposed to validate:

  * G1: universe mismatch — a 1-fold / 5-trading-day OOS backtest on an
        unrelated hand-picked ticker unlocked a strategy whose own universe
        loses money over the same window. FIXED: the blocking gate is now
        ``enable_gate()`` (structural evidence-fit checks), and B3a's §1
        contract refuses a 5-day OOS window at create time.
  * G2: the caller can pin ``engine_mode="council"`` on a risk_parity strategy
        (serializer only auto-routes when engine_mode is absent). Still true at
        the serializer, but ``enable_gate``'s engine_matches_kind check blocks
        such a run from arming the autopilot.
  * G3: ``search_space`` from the caller OVERRIDES the strategy's leverage /
        regime-gate config (``{**pd_cfg, **caller_ss}``), so a 2x levered
        strategy is "validated" by an unlevered backtest. STILL OPEN — owned by
        the gate WP, not B3a.
  * G4: the gate had no minimum-evidence rule — a single fold with 4 daily
        returns was enough. FIXED: ``enable_gate`` requires MIN_FOLDS folds and
        MIN_OOS_SESSIONS sessions, and oos_window_days >= 21 at create time.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from apps.backtests.models import Backtest, BacktestMetrics
from apps.backtests.serializers import BacktestCreateSerializer
from apps.backtests.walkforward import run_walkforward
from apps.data.models import DailyBar
from apps.portfolios.models import Portfolio, PortfolioStrategy, Universe, UniverseMembership
from apps.portfolios.validation import enable_gate, validation_status

User = get_user_model()
pytestmark = pytest.mark.django_db

START = dt.date(2023, 1, 2)


def _seed_path(ticker: str, start: dt.date, n_cal_days: int, steps: list[float]) -> None:
    """Deterministic price path: consecutive trading-day returns cycle through
    ``steps`` (all-positive => strict uptrend; all-negative => strict downtrend)."""
    price = 100.0
    k = 0
    for i in range(n_cal_days):
        d = start + dt.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        price *= 1.0 + steps[k % len(steps)]
        k += 1
        DailyBar.objects.create(
            ticker=ticker, date=d, source="fmp",
            open=Decimal(f"{price:.4f}"), high=Decimal(f"{price * 1.001:.4f}"),
            low=Decimal(f"{price * 0.999:.4f}"), close=Decimal(f"{price:.4f}"),
            adjusted_close=Decimal(f"{price:.4f}"), volume=1_000_000,
        )


def _rp_strategy(user, *, tickers=("AAA",), **kw):
    u = Universe.objects.create(name=f"review-uni-{kw.get('name', 'rp')}")
    for t in tickers:
        UniverseMembership.objects.create(universe=u, ticker=t, effective_from=dt.date(2020, 1, 1))
    pf = Portfolio.objects.create(
        user=user, kind=Portfolio.KIND_STRATEGY, name=kw.get("name", "rp"),
        cash_balance=Decimal("100000"),
    )
    fields = dict(
        user=user, name=kw.get("name", "rp"), universe=u, portfolio=pf,
        kind=PortfolioStrategy.KIND_RISK_PARITY, personas=[],
    )
    fields.update({k: v for k, v in kw.items() if k != "name"})
    return PortfolioStrategy.objects.create(**fields)


def _create_via_serializer(user, data: dict) -> Backtest:
    ser = BacktestCreateSerializer(data=data, context={"request": SimpleNamespace(user=user)})
    assert ser.is_valid(), ser.errors
    return ser.save(user=user)


@pytest.fixture
def user(db):
    return User.objects.create_user(email="review-gate@x.test", password="pw-fake-123456789")


def test_G1_G4_gate_blocks_unrelated_ticker_and_thin_evidence(user):
    """The strategy trades AAA (a strict downtrend). The 'validation' backtest
    trades ZZZ (a strict uptrend) for ONE short fold. ``validation_status()``
    still reports passed=True (it only looks at the metrics), but the BLOCKING
    ``enable_gate()`` refuses it on universe mismatch + thin evidence."""
    n_days = 160
    _seed_path("AAA", START, n_days, [-0.001, -0.002, -0.003])   # the strategy's universe: losing
    _seed_path("ZZZ", START, n_days, [0.001, 0.002, 0.003])      # cherry-picked: winning

    strategy = _rp_strategy(user, tickers=("AAA",), name="rp-g1")

    # master = 147 calendar days == is_window(126) + oos_window(21): exactly one
    # fold. B3a §1 refuses anything shorter (oos_window_days >= 21), so the
    # 5-trading-day OOS this test used to build is no longer creatable at all.
    end = START + dt.timedelta(days=147)
    bt = _create_via_serializer(user, {
        "name": "cherry", "universe": ["ZZZ"],
        "start_date": START.isoformat(), "end_date": end.isoformat(),
        "is_window_days": 126, "oos_window_days": 21, "step_days": 21,
        "rebalance_frequency": "monthly", "strategy_id": strategy.id,
    })
    assert bt.engine_mode == Backtest.RISK_PARITY  # auto-routed from the kind
    assert bt.universe == ["ZZZ"]                 # the serializer does not check it...

    run_walkforward(bt)
    bt.refresh_from_db()
    assert bt.status == Backtest.DONE
    assert bt.folds.count() == 1

    m = BacktestMetrics.objects.get(backtest=bt)
    assert float(m.mean_oos_sharpe) > 0 and float(m.sharpe) > 0
    assert validation_status(strategy)["backtest_id"] == bt.id

    # ...the blocking gate does. Universe mismatch AND not enough evidence.
    gate = enable_gate(strategy)
    assert gate["passed"] is False, gate
    reasons = " ".join(gate["reasons"])
    assert "not the strategy's universe" in reasons
    assert "walk-forward fold" in reasons or "out-of-sample session" in reasons


def test_G1_G4_create_refuses_a_five_day_oos_window(user):
    """The exact shape the old proof used — one fold, 5 trading days of OOS —
    is now a 400 at the create boundary."""
    strategy = _rp_strategy(user, tickers=("AAA",), name="rp-g1b")
    ser = BacktestCreateSerializer(
        data={
            "name": "cherry", "universe": ["ZZZ"],
            "start_date": START.isoformat(),
            "end_date": (START + dt.timedelta(days=131)).isoformat(),
            "is_window_days": 126, "oos_window_days": 5, "step_days": 5,
            "rebalance_frequency": "monthly", "strategy_id": strategy.id,
        },
        context={"request": SimpleNamespace(user=user)},
    )
    assert not ser.is_valid()
    assert "oos_window_days" in ser.errors


def test_G2_caller_can_pin_council_engine_on_risk_parity_strategy(user):
    strategy = _rp_strategy(user, name="rp-g2")
    ser = BacktestCreateSerializer(
        data={
            "name": "x", "universe": ["AAA"],
            "start_date": "2023-01-02", "end_date": "2025-12-31",
            "strategy_id": strategy.id, "engine_mode": "council",
        },
        context={"request": SimpleNamespace(user=user)},
    )
    assert ser.is_valid(), ser.errors
    # The run that becomes gate evidence for a risk_parity strategy is a council
    # (LLM-vote) backtest — it does not model how the strategy trades live.
    assert ser.validated_data["engine_mode"] == "council"
    assert "sizing" not in (ser.validated_data.get("search_space") or {})


def test_G3_caller_search_space_overrides_strategy_leverage_and_regime_gate(user):
    strategy = _rp_strategy(
        user, name="rp-g3",
        rp_vol_target_annual=Decimal("0.12"), rp_max_gross=Decimal("2.00"),
        enable_spy_regime_gate=True, regime_gate_floor=Decimal("0.5"),
    )
    ser = BacktestCreateSerializer(
        data={
            "name": "x", "universe": ["AAA"],
            "start_date": "2023-01-02", "end_date": "2025-12-31",
            "strategy_id": strategy.id,
            "search_space": {
                "max_gross": 1.0, "rp_vol_target_annual": 0.0,
                "enable_spy_regime_gate": False,
            },
        },
        context={"request": SimpleNamespace(user=user)},
    )
    assert ser.is_valid(), ser.errors
    ss = ser.validated_data["search_space"]
    # Strategy says 2x levered + regime gate on; the stored backtest config says
    # unlevered + gate off — and the gate later reports "reflects current config".
    assert ss["max_gross"] == 1.0
    assert ss["rp_vol_target_annual"] == 0.0
    assert ss["enable_spy_regime_gate"] is False


def test_G5_universe_tickers_without_bars_are_silently_dropped_but_gate_blocks(user):
    """Strategy universe = AAA + NEWX; NEWX has no bars at all. The validation
    backtest still runs on AAA alone and completes DONE with
    prime_completeness=1.0 and no warning (unchanged engine behaviour), and
    validation_status still passes on the metrics — but the blocking
    ``enable_gate()`` refuses it on thin evidence."""
    _seed_path("AAA", START, 160, [0.001, 0.002, 0.003])
    strategy = _rp_strategy(user, tickers=("AAA", "NEWX"), name="rp-g5")
    end = START + dt.timedelta(days=147)
    bt = _create_via_serializer(user, {
        "name": "partial", "universe": ["AAA", "NEWX"],
        "start_date": START.isoformat(), "end_date": end.isoformat(),
        "is_window_days": 126, "oos_window_days": 21, "step_days": 21,
        "rebalance_frequency": "monthly", "strategy_id": strategy.id,
    })
    run_walkforward(bt)
    bt.refresh_from_db()
    assert bt.status == Backtest.DONE
    assert bt.prime_completeness == 1.0 and bt.error_message == ""
    held = {p["ticker"] for d in bt.days.all() for p in d.positions}
    assert held == {"AAA"}                                   # NEWX never traded
    assert validation_status(strategy)["passed"] is True     # metrics-only view
    gate = enable_gate(strategy)
    assert gate["passed"] is False, gate                     # blocking view
    assert any("session" in r or "fold" in r for r in gate["reasons"]), gate["reasons"]


def test_G6_no_bars_at_all_completes_DONE_with_zero_metrics(user):
    """A universe with no price data does not fail — it 'completes' with an
    empty curve and zeroed metrics (n days = 0), indistinguishable in status
    from a real run."""
    bt = _create_via_serializer(user, {
        "name": "empty", "universe": ["NODATA"],
        "start_date": START.isoformat(), "end_date": "2025-12-31",
        "engine_mode": "risk_parity",
    })
    run_walkforward(bt)
    bt.refresh_from_db()
    assert bt.status == Backtest.DONE
    assert bt.days.count() == 0 and bt.folds.count() >= 1
    m = BacktestMetrics.objects.get(backtest=bt)
    assert float(m.sharpe) == 0.0 and float(m.total_return_pct) == 0.0
    assert bt.error_message == ""
