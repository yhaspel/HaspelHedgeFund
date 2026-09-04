"""P7 — fund onboarding flow: per-account setup hints, the demo validation-backtest
seed, the strategy-linked backtest create, and the seed→enable→next_run loop.

Covers the fixes for the 'armed but un-enable-able / no next run / unclear how to
enable' fund-dashboard report.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.backtests.models import Backtest
from apps.backtests.seed import seed_validation_backtest
from apps.backtests.serializers import BacktestCreateSerializer
from apps.brokers.models import BrokerAccount, StrategyBrokerLink
from apps.portfolios import fund as fund_layer
from apps.portfolios import sleeves
from apps.portfolios.models import (
    AutonomousFund,
    Portfolio,
    PortfolioStrategy,
    StrategyAutopilot,
    Universe,
    UniverseMembership,
)
from apps.portfolios.validation import validation_status

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="p7flow@x.test", password="pw-fake-123456789")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _strategy(user, name="S", **kw):
    u = Universe.objects.create(name=f"flow-uni-{name}")
    UniverseMembership.objects.create(universe=u, ticker="AAPL", effective_from=dt.date(2020, 1, 1))
    pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_STRATEGY, name=name)
    return PortfolioStrategy.objects.create(
        user=user, name=name, universe=u, portfolio=pf,
        kind=PortfolioStrategy.KIND_LONG_SHORT, **kw,
    )


def _account(user, label="A", cash="100000"):
    pf = Portfolio.objects.create(
        user=user, kind=Portfolio.KIND_BROKER, name=f"bk-{label}", cash_balance=Decimal(cash),
    )
    return BrokerAccount.objects.create(
        user=user, broker="mock", mode=BrokerAccount.MODE_PAPER,
        account_id=f"mock-{label}", label=label, portfolio=pf,
        connection_status=BrokerAccount.STATUS_ACTIVE,
    )


def _real_validation_backtest(strategy):
    """A real-shaped DONE total-return-era backtest that clears the P10-hardened
    §9 gate (positive mean-of-folds AND stitched Sharpe, DD within halt). This
    is what seeds used to fabricate; post-§B3 only real runs open the gate."""
    from apps.backtests.models import BacktestMetrics

    bt = Backtest.objects.create(
        user=strategy.user, strategy=strategy, name="real validation WF",
        start_date=dt.date(2024, 1, 1), end_date=dt.date(2025, 12, 31),
        status=Backtest.DONE,
    )
    BacktestMetrics.objects.create(
        backtest=bt, mean_oos_sharpe=Decimal("0.9"), sharpe=Decimal("0.7"),
        max_drawdown_pct=Decimal("4.0"),
    )
    return bt


def _disabled_fund_of_three(user):
    """P14: one shared account, three member sleeves (autopilots created disabled
    by the roster write, funded by Reset)."""
    fund = AutonomousFund.objects.create(owner=user, name="Autonomous Fund")
    sleeves.configure_account(fund, _account(user, label="POOL", cash="300000"))
    members = [
        {"strategy_id": _strategy(user, name=f"S{i}").id, "allocation_pct": str(pct)}
        for i, pct in enumerate(sleeves.equal_split(3))
    ]
    sleeves.set_members(fund, members)
    sleeves.reset_fund(fund)
    fund.refresh_from_db()
    return fund


# --------------------------------------------------------------------------
# fund_overview setup hints — the honest-card data.
# --------------------------------------------------------------------------
def test_per_account_hint_points_to_backtest_when_unvalidated(user):
    fund = _disabled_fund_of_three(user)               # links + disabled APs, no backtest
    out = fund_layer.fund_overview(fund)
    assert out["is_live"] is False
    for p in out["members"]:
        assert p["validation_passed"] is False
        assert p["can_enable"] is False
        assert "backtest" in p["setup_hint"].lower()   # tells the user the next step


def test_per_account_can_enable_when_validated_but_off(user):
    fund = _disabled_fund_of_three(user)
    s = fund.strategies.first()
    _real_validation_backtest(s)                        # gate now passes, still disabled
    out = fund_layer.fund_overview(fund)
    card = next(p for p in out["members"] if p["strategy_id"] == s.id)
    assert card["validation_passed"] is True
    assert card["can_enable"] is True
    assert "enable" in card["setup_hint"].lower()


def test_per_account_enabled_has_no_hint(user):
    fund = _disabled_fund_of_three(user)
    StrategyAutopilot.objects.filter(strategy__in=fund.strategies.all()).update(is_enabled=True)
    out = fund_layer.fund_overview(fund)
    for p in out["members"]:
        assert p["is_enabled"] is True
        assert p["setup_hint"] is None
        assert p["can_enable"] is False


# --------------------------------------------------------------------------
# Demo seed — P10 §B3: synthetic, never §9-gate evidence, still idempotent.
# --------------------------------------------------------------------------
def test_seed_is_synthetic_and_does_not_unlock_gate(user):
    s = _strategy(user)
    StrategyAutopilot.objects.create(strategy=s, dd_hard_halt_pct=Decimal("7.5"))
    assert validation_status(s)["passed"] is False
    bt = seed_validation_backtest(s)
    assert bt is not None
    assert bt.status == Backtest.SYNTHETIC
    assert bt.strategy_id == s.id
    # Fabricated rows can no longer arm live trading.
    assert validation_status(s)["passed"] is False


def test_seed_is_idempotent(user):
    s = _strategy(user)
    StrategyAutopilot.objects.create(strategy=s)
    assert seed_validation_backtest(s) is not None
    assert seed_validation_backtest(s) is None         # seed already exists → no dup
    assert Backtest.objects.filter(strategy=s).count() == 1


def test_seed_dd_shaped_within_tight_hard_halt(user):
    """The demo seed still shapes its (cosmetic) max DD within the hard-halt
    limit so the record reads plausibly — but it never opens the gate."""
    s = _strategy(user)
    StrategyAutopilot.objects.create(strategy=s, dd_hard_halt_pct=Decimal("2.0"))
    bt = seed_validation_backtest(s)
    assert bt.metrics.max_drawdown_pct < Decimal("2.0")
    assert validation_status(s)["passed"] is False


# --------------------------------------------------------------------------
# real backtest → enable → next_run_at populates (end to end).
# --------------------------------------------------------------------------
def test_real_backtest_then_enable_populates_next_run(client, user):
    s = _strategy(user)
    acc = _account(user)
    StrategyBrokerLink.objects.create(strategy=s, broker_account=acc)
    StrategyAutopilot.objects.create(
        strategy=s, broker_account=acc, dd_hard_halt_pct=Decimal("7.5"),
    )
    _real_validation_backtest(s)
    r = client.post(f"/api/strategies/{s.id}/autopilot/enable/")
    assert r.status_code == 200
    s.refresh_from_db()
    assert s.autopilot.is_enabled is True
    assert s.autopilot.next_run_at is not None          # schedule now live


# --------------------------------------------------------------------------
# Backtest create can link a strategy (powers the Autopilot 'Run validation
# backtest' deep link) — and only the caller's own strategy.
# --------------------------------------------------------------------------
def test_backtest_create_serializer_links_own_strategy(user):
    s = _strategy(user)
    req = SimpleNamespace(user=user)
    ser = BacktestCreateSerializer(
        data={
            "name": "WF", "universe": ["AAPL"],
            "start_date": "2023-01-02", "end_date": "2025-12-31",
            "strategy_id": s.id,
        },
        context={"request": req},
    )
    assert ser.is_valid(), ser.errors
    bt = ser.save(user=user)
    assert bt.strategy_id == s.id


def test_backtest_create_serializer_rejects_other_users_strategy(user, db):
    other = User.objects.create_user(email="other@x.test", password="pw-fake-123456789")
    s_other = _strategy(other, name="theirs")
    req = SimpleNamespace(user=user)
    ser = BacktestCreateSerializer(
        data={
            "name": "WF", "universe": ["AAPL"],
            "start_date": "2023-01-02", "end_date": "2025-12-31",
            "strategy_id": s_other.id,
        },
        context={"request": req},
    )
    assert not ser.is_valid()
    assert "strategy_id" in ser.errors
