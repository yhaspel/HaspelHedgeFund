"""WP B3b — tests for the fixes that had no proof test of their own:

* ``release_caps.evaluate_release_caps`` — shadow-mode daily caps at release,
* ``autopilot_audit.record_release_outcome`` — release outcomes on the run,
* ``autopilot_audit.disable_on_config_change`` — the ``config_changed`` disarm,
* the daily caps' ``0 means zero`` semantics,
* the fund member payload additions (``timezone`` / ``caps_shadow`` /
  ``validation_warnings``),
* ``validation.enable_gate`` passing on gate-grade evidence.

Run:
  DJANGO_SETTINGS_MODULE=hedgefund.settings.test /tmp/v312/bin/pytest \
      tests/test_fix_b3b_release_caps_and_payload.py -q -p no:cacheprovider
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.brokers import demo_fills
from apps.brokers.adapters import mock as mock_adapter
from apps.brokers.models import BrokerAccount, BrokerOrder
from apps.portfolios import autopilot as bridge
from apps.portfolios import fund as fund_layer
from apps.portfolios import sleeves
from apps.portfolios.autopilot_audit import (
    disable_on_config_change,
    last_caps_shadow,
    record_release_outcome,
    snapshot_risk_config,
)
from apps.portfolios.models import (
    AutonomousFund,
    AutopilotRun,
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    RebalanceOrder,
    StrategyAutopilot,
    Universe,
    UniverseMembership,
)
from apps.portfolios.release_caps import evaluate_release_caps
from apps.portfolios.validation import enable_gate, expected_engine_mode

User = get_user_model()


@pytest.fixture(autouse=True)
def _reset_mock():
    mock_adapter.reset_state()
    yield
    mock_adapter.reset_state()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="fix-b3b@x.test", password="pw-fake-123456789")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture
def price_200(monkeypatch):
    monkeypatch.setattr(demo_fills, "live_price", lambda account, ticker: Decimal("200"))


def _strategy(user, name="S", **kw):
    u = Universe.objects.create(name=f"b3b-uni-{name}")
    for t in ("AAPL", "MSFT", "XLE"):
        UniverseMembership.objects.create(
            universe=u, ticker=t, sector="Tech", effective_from=dt.date(2020, 1, 1),
        )
    pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_STRATEGY, name=name)
    defaults = dict(
        kind=PortfolioStrategy.KIND_LONG_SHORT,
        max_position_pct=Decimal("0.10"),
        max_sector_pct=Decimal("0.50"),
        min_trade_notional_usd=Decimal("100"),
        max_turnover_pct=Decimal("1.0"),
        auto_run_council=True,
    )
    defaults.update(kw)
    return PortfolioStrategy.objects.create(
        user=user, name=name, universe=u, portfolio=pf, **defaults,
    )


def _account(user, *, broker="mock", cash="100000", label="POOL"):
    pf = Portfolio.objects.create(
        user=user, kind=Portfolio.KIND_BROKER, name=f"bk-{label}", cash_balance=Decimal(cash),
    )
    acc = BrokerAccount.objects.create(
        user=user, broker=broker, mode=BrokerAccount.MODE_PAPER,
        account_id=f"{broker}-{label}-{user.id}", label=label, portfolio=pf,
        connection_status=BrokerAccount.STATUS_ACTIVE,
    )
    if broker == "mock":
        mock_adapter.seed_demo_book(acc, cash=Decimal(cash))
    return acc


def _fund(user, allocations, *, cash="100000", broker="mock", enable=True, **skw):
    fund = AutonomousFund.objects.create(owner=user, name="Autonomous Fund")
    sleeves.configure_account(fund, _account(user, broker=broker, cash=cash))
    strategies = {name: _strategy(user, name=name, **skw) for name in allocations}
    sleeves.set_members(fund, [
        {"strategy_id": strategies[n].id, "allocation_pct": str(p)} for n, p in allocations.items()
    ])
    sleeves.reset_fund(fund)
    if enable:
        StrategyAutopilot.objects.filter(strategy__in=strategies.values()).update(is_enabled=True)
    fund.refresh_from_db()
    return fund, strategies


def _done_target(strategy, weights, *, price="200"):
    n = PortfolioTarget.objects.filter(strategy=strategy).count()
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=dt.date(2026, 9, 4) + dt.timedelta(days=n),
        status=PortfolioTarget.DONE, target_weights=weights,
    )
    for t in weights:
        RebalanceOrder.objects.create(
            target=target, ticker=t, side="buy", quantity=Decimal("1"),
            limit_price=Decimal(price), reason="open",
            estimated_notional_usd=Decimal(price), sequence=2,
        )
    return target


def _gate_grade_backtest(strategy, **metrics):
    from apps.backtests.models import Backtest, BacktestFold, BacktestMetrics

    tickers = list(
        UniverseMembership.objects
        .filter(universe=strategy.universe, effective_to__isnull=True)
        .values_list("ticker", flat=True)
    )
    bt = Backtest.objects.create(
        user=strategy.user, strategy=strategy, name="gate bt",
        universe=tickers, engine_mode=expected_engine_mode(strategy),
        start_date=dt.date(2022, 1, 3), end_date=dt.date(2025, 1, 3),
        is_window_days=252, oos_window_days=63, step_days=63,
        status=Backtest.DONE,
    )
    for i in range(6):
        BacktestFold.objects.create(
            backtest=bt, fold_index=i,
            is_start=dt.date(2022, 1, 3), is_end=dt.date(2022, 12, 30),
            oos_start=dt.date(2023, 1, 3), oos_end=dt.date(2023, 3, 31),
        )
    BacktestMetrics.objects.create(
        backtest=bt,
        **{"mean_oos_sharpe": Decimal("0.8"), "max_drawdown_pct": Decimal("4.0"),
           "sharpe": Decimal("0.7"), **metrics},
    )
    return bt


# ---------------------------------------------------------------------------
# Daily caps: 0 means ZERO (was "unlimited").
# ---------------------------------------------------------------------------
def test_zero_daily_order_cap_blocks_every_order(user, price_200):
    fund, s = _fund(user, {"A": 100})
    StrategyAutopilot.objects.filter(strategy=s["A"]).update(max_orders_per_day=0)
    s["A"].autopilot.refresh_from_db()
    dec = bridge.maybe_emit_and_submit(
        _done_target(s["A"], {"AAPL": 0.10}), autopilot=s["A"].autopilot,
    )
    assert dec["submitted"] == 0
    assert [i["skipped"] for i in dec["items"]] == ["daily order cap"]
    assert BrokerOrder.objects.count() == 0


def test_zero_daily_notional_cap_blocks_every_order(user, price_200):
    fund, s = _fund(user, {"A": 100})
    StrategyAutopilot.objects.filter(strategy=s["A"]).update(
        max_notional_per_day_usd=Decimal("0"),
    )
    s["A"].autopilot.refresh_from_db()
    dec = bridge.maybe_emit_and_submit(
        _done_target(s["A"], {"AAPL": 0.10}), autopilot=s["A"].autopilot,
    )
    assert dec["submitted"] == 0
    assert [i["skipped"] for i in dec["items"]] == ["daily notional cap"]


# ---------------------------------------------------------------------------
# Shadow-mode release caps.
# ---------------------------------------------------------------------------
def test_evaluate_release_caps_flags_over_cap_orders_without_blocking(user, price_200, monkeypatch):
    fund, s = _fund(user, {"A": 100}, broker="alpaca_paper")
    acct = fund.broker_account
    ap = s["A"].autopilot
    StrategyAutopilot.objects.filter(pk=ap.pk).update(max_orders_per_day=1)
    ap.refresh_from_db()
    monkeypatch.setattr("apps.brokers.market_calendar.is_market_open", lambda *a, **k: False)
    bridge.maybe_emit_and_submit(
        _done_target(s["A"], {"AAPL": 0.10, "MSFT": 0.10}), autopilot=ap,
    )
    held = list(BrokerOrder.objects.filter(status=BrokerOrder.STATUS_PENDING_OPEN).order_by("id"))
    assert len(held) == 2

    out = evaluate_release_caps(acct, held)
    assert out["shadow"] is True
    assert out["would_skip"] == [held[1].pk]              # the 2nd busts the 1/day cap
    assert "daily order cap" in out["reason"]
    assert out["orders"] == 2                            # the day's total, basis + batch
    # Notional is priced off the order's real sizing, not the $100 placeholder.
    assert out["notional"] == Decimal("20000.00")
    # ...and nothing was blocked or mutated.
    for order in held:
        order.refresh_from_db()
        assert order.status == BrokerOrder.STATUS_PENDING_OPEN


def test_record_release_outcome_writes_the_run_summary(user, price_200, monkeypatch):
    fund, s = _fund(user, {"A": 100}, broker="alpaca_paper")
    ap = s["A"].autopilot
    run = AutopilotRun.objects.create(
        autopilot=ap, fire_time_utc=timezone.now(), status=AutopilotRun.RUNNING,
    )
    monkeypatch.setattr("apps.brokers.market_calendar.is_market_open", lambda *a, **k: False)
    bridge.maybe_emit_and_submit(_done_target(s["A"], {"AAPL": 0.10}), autopilot=ap)
    order = BrokerOrder.objects.get()
    run.refresh_from_db()
    assert run.submit_decision["pending_open"] == 1

    BrokerOrder.objects.filter(pk=order.pk).update(status=BrokerOrder.STATUS_FILLED)
    caps = {"would_skip": [], "reason": "", "orders": 1, "notional": Decimal("10000.00")}
    rec = record_release_outcome(run.pk, released=[order.pk], caps_shadow=caps)
    assert rec["released"] == [order.pk]

    run.refresh_from_db()
    assert run.submit_decision["release"][-1]["released"] == [order.pk]
    assert run.submit_decision["caps_shadow"]["notional"] == "10000.00"
    assert run.submit_decision["items"][0]["status"] == "filled"
    assert run.submit_decision["pending_open"] == 0
    assert last_caps_shadow(ap)["notional"] == "10000.00"


# ---------------------------------------------------------------------------
# Fund member payload additions.
# ---------------------------------------------------------------------------
def test_member_payload_exposes_timezone_caps_shadow_and_warnings(user, price_200):
    fund, s = _fund(user, {"A": 100})
    ap = s["A"].autopilot
    StrategyAutopilot.objects.filter(pk=ap.pk).update(timezone="Europe/London")
    AutopilotRun.objects.create(
        autopilot=ap, fire_time_utc=timezone.now(), status=AutopilotRun.SUBMITTED,
        submit_decision={"caps_shadow": {"would_skip": [7], "reason": "daily order cap (1)"}},
    )
    member = fund_layer.fund_overview(fund)["members"][0]
    assert member["timezone"] == "Europe/London"
    assert member["caps_shadow"]["would_skip"] == [7]
    # No backtest at all → the §9 evidence-fit warnings say so (non-blocking).
    assert member["validation_warnings"] and member["is_enabled"] is True

    _gate_grade_backtest(s["A"])
    member = fund_layer.fund_overview(fund)["members"][0]
    assert member["validation_warnings"] == []


# ---------------------------------------------------------------------------
# §9 enable gate — the positive case, and the config_changed disarm.
# ---------------------------------------------------------------------------
def test_enable_gate_passes_on_gate_grade_evidence(user, price_200, client):
    fund, s = _fund(user, {"A": 100}, enable=False)
    _gate_grade_backtest(s["A"])
    gate = enable_gate(s["A"])
    assert gate["passed"] is True, gate["reasons"]
    assert gate["warnings"] == []
    r = client.post(f"/api/strategies/{s['A'].id}/autopilot/enable/")
    assert r.status_code == 200, r.json()


def test_disable_on_config_change_is_a_no_op_for_a_cosmetic_edit(user, price_200):
    fund, s = _fund(user, {"A": 100})
    strategy = s["A"]
    before = snapshot_risk_config(strategy)
    strategy.name = "renamed"
    strategy.save(update_fields=["name"])
    assert disable_on_config_change(strategy, before) == []
    strategy.autopilot.refresh_from_db()
    assert strategy.autopilot.is_enabled is True

    before = snapshot_risk_config(strategy)
    strategy.max_position_pct = Decimal("0.50")
    strategy.save(update_fields=["max_position_pct"])
    assert disable_on_config_change(strategy, before) == ["max_position_pct"]
    strategy.autopilot.refresh_from_db()
    assert strategy.autopilot.is_enabled is False
    assert strategy.autopilot.next_run_at is None
