"""Adversarial review (reviewer: fund) — proof tests for the fund / autopilot
layer. Each test documents ONE finding; the docstring names it.

Run:
  DJANGO_SETTINGS_MODULE=hedgefund.settings.test /tmp/v312/bin/pytest \
      tests/test_review_fund_halt_and_cycles.py -q -p no:cacheprovider
"""
from __future__ import annotations

import datetime as dt
import types
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
from apps.portfolios import sleeves, tasks, tasks_autopilot
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

User = get_user_model()


@pytest.fixture(autouse=True)
def _reset_mock():
    mock_adapter.reset_state()
    yield
    mock_adapter.reset_state()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="rev-fund@x.test", password="pw-fake-123456789")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture
def price_200(monkeypatch):
    monkeypatch.setattr(demo_fills, "live_price", lambda account, ticker: Decimal("200"))


def _strategy(user, name="S", **kw):
    u = Universe.objects.create(name=f"rev-uni-{name}")
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


def _account(user, *, broker="mock", cash="100000", label="POOL", mode=BrokerAccount.MODE_PAPER):
    pf = Portfolio.objects.create(
        user=user, kind=Portfolio.KIND_BROKER, name=f"bk-{label}", cash_balance=Decimal(cash),
    )
    acc = BrokerAccount.objects.create(
        user=user, broker=broker, mode=mode, account_id=f"{broker}-{label}-{user.id}",
        label=label, portfolio=pf, connection_status=BrokerAccount.STATUS_ACTIVE,
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


def _cycle(strategy, weights, **kw):
    ap = strategy.autopilot
    return bridge.maybe_emit_and_submit(_done_target(strategy, weights, **kw), autopilot=ap)


def _positions(book):
    return {p.ticker: Decimal(str(p.quantity)) for p in book.positions.all()}


def _gate_grade_backtest(strategy, **metrics):
    """A DONE total-return backtest that clears the HARDENED §9 gate: the
    strategy's own universe, the engine its kind trades live on, ≥6 walk-forward
    folds and ≥120 OOS sessions."""
    from apps.backtests.models import Backtest, BacktestFold, BacktestMetrics
    from apps.portfolios.validation import expected_engine_mode

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


# ===========================================================================
# F1 (FIXED) — the fund kill switch (POST /api/fund/halt/) survives the hourly
# guardrail_sweep: evaluate_drawdown() may RAISE severity but never lowers it
# out of HALTED, and the dispatcher reads fund.state — a halted fund's members
# record `skipped: fund halted` instead of firing. POST /api/fund/resume/ is
# the only exit.
# ===========================================================================
def test_F1_fund_halt_is_reverted_by_guardrail_sweep_and_members_dispatch(
    user, price_200, monkeypatch,
):
    fund, s = _fund(user, {"A": 50, "B": 50})
    # Seed peaks (as a prior sweep would) so the next evaluation is a normal one.
    for st in s.values():
        ap = st.autopilot
        ap.peak_equity_usd = Decimal("50000")
        ap.is_market_aware = False
        ap.next_run_at = timezone.now() - dt.timedelta(minutes=1)   # due
        ap.save()

    n = fund_layer.halt_fund(fund, reason="manual_kill_switch")
    assert n == 2
    fund.refresh_from_db()
    assert fund.state == AutonomousFund.STATE_HALTED
    assert set(StrategyAutopilot.objects.values_list("state", flat=True)) == {"halted"}

    # The hourly sweep runs (no drawdown: sleeves sit at their $50k peak).
    monkeypatch.setattr("apps.brokers.market_calendar.is_market_open", lambda *a, **k: False)
    tasks_autopilot.guardrail_sweep()

    fund.refresh_from_db()
    # The fund still says halted...
    assert fund.state == AutonomousFund.STATE_HALTED
    states = set(StrategyAutopilot.objects.values_list("state", flat=True))
    assert states == {"halted"}, states                               # ...and so does every member

    # ...so nothing is even due: the dispatcher skips halted members.
    fired: list[int] = []
    monkeypatch.setattr(tasks_autopilot.run_autopilot_cycle, "delay", lambda rid: fired.append(rid))
    out = tasks_autopilot.dispatch_due_autopilots()
    assert out["due"] == 0 and out["dispatched"] == 0, out
    assert fired == [] and AutopilotRun.objects.count() == 0

    # Defense in depth: even with the member rows forced ACTIVE (a DB/admin
    # edit, a stale row), the dispatcher reads fund.state and records why.
    StrategyAutopilot.objects.update(state=StrategyAutopilot.STATE_ACTIVE)
    out = tasks_autopilot.dispatch_due_autopilots()
    assert out["dispatched"] == 0 and out["skipped_fund_halt"] == 2, out
    assert fired == []
    runs = list(AutopilotRun.objects.all())
    assert len(runs) == 2
    assert {r.status for r in runs} == {AutopilotRun.SKIPPED}
    assert all(r.submit_decision["skipped"] == "fund halted" for r in runs)


def test_F1b_per_strategy_resume_bypasses_a_fund_halt(client, user, price_200, monkeypatch):
    """Second path (FIXED): the member-level Resume refuses while the fund is
    halted, and even a forced-active member emits nothing."""
    fund, s = _fund(user, {"A": 100})
    fund_layer.halt_fund(fund, reason="manual_kill_switch")
    r = client.post(f"/api/strategies/{s['A'].id}/autopilot/resume/")
    assert r.status_code == 409, r.json()
    assert "/api/fund/resume/" in r.json()["detail"]
    s["A"].autopilot.refresh_from_db()
    assert s["A"].autopilot.state == StrategyAutopilot.STATE_HALTED
    fund.refresh_from_db()
    assert fund.state == AutonomousFund.STATE_HALTED

    # Nothing reaches the broker, even from a member forced back to active.
    StrategyAutopilot.objects.update(state=StrategyAutopilot.STATE_ACTIVE)
    s["A"].autopilot.refresh_from_db()
    target = _done_target(s["A"], {"AAPL": 0.10})
    dec = bridge._finalize_target(target)
    assert dec is not None and dec["skipped"] == "fund halted", dec
    assert dec["submitted"] == 0 and dec["orders"] == 0
    assert BrokerOrder.objects.count() == 0


def test_F1c_sweep_clears_the_drift_and_auto_run_council_halts_too(user, price_200, monkeypatch):
    """FIXED: the pre-flight halts (drift_beyond_tolerance / auto_run_council
    off) are written as state=HALTED and the sweep no longer un-halts them."""
    fund, s = _fund(user, {"A": 100})
    ap = s["A"].autopilot
    ap.peak_equity_usd = Decimal("100000")
    ap.save()
    # What run_autopilot_cycle does on drift_beyond_tolerance:
    StrategyAutopilot.objects.filter(pk=ap.pk).update(state=StrategyAutopilot.STATE_HALTED)
    monkeypatch.setattr("apps.brokers.market_calendar.is_market_open", lambda *a, **k: False)
    tasks_autopilot.guardrail_sweep()
    ap.refresh_from_db()
    assert ap.state == StrategyAutopilot.STATE_HALTED                 # halt is latched


# ===========================================================================
# F2 (FIXED) — in-flight awareness: a second cycle while the previous batch is
# still pending_open (Friday fire → orders held over the weekend/holiday) is
# skipped whole (`skipped_pending_open`) instead of re-emitting the same
# orders. Nothing partial: either the batch goes out or nothing does.
# ===========================================================================
def test_F2_second_cycle_while_orders_pending_open_double_emits(user, price_200, monkeypatch):
    fund, s = _fund(user, {"A": 100}, broker="alpaca_paper")
    monkeypatch.setattr("apps.brokers.market_calendar.is_market_open", lambda *a, **k: False)
    acct = fund.broker_account

    dec1 = _cycle(s["A"], {"AAPL": 0.10})
    assert dec1["pending_open"] == 1 and dec1["submitted"] == 0
    held1 = BrokerOrder.objects.get(broker_account=acct)
    assert held1.status == BrokerOrder.STATUS_PENDING_OPEN
    assert Decimal(str(held1.quantity)) == Decimal("50")   # 10% of $100k @ $200

    # Next cycle (a run-now, a cron edit, or simply the next fire before the
    # first batch released): the held batch blocks it.
    dec2 = _cycle(s["A"], {"AAPL": 0.10})
    assert dec2["skipped"] == "skipped_pending_open", dec2
    assert dec2["orders"] == 0 and dec2["submitted"] == 0 and dec2["pending_open"] == 0
    assert dec2["pending_open_order_ids"] == [held1.id]
    assert "still held for the open" in dec2["message"]
    held = list(BrokerOrder.objects.filter(
        broker_account=acct, ticker="AAPL", status=BrokerOrder.STATUS_PENDING_OPEN,
    ))
    assert len(held) == 1                                  # 1× the target, not 2×
    assert sum(Decimal(str(o.quantity)) for o in held) == Decimal("50")


# ===========================================================================
# F3 (FIXED) — the strategy-page "Run now" (POST /api/strategies/<pk>/run-now/)
# drives the same terminal hook as the autopilot, so on an ENABLED fund member
# it used to emit real orders with no AutopilotRun (no audit, no daily-cap
# basis, no pre-flight reconcile / drawdown check) — and with a caller-supplied
# as_of_date it sized the sleeve at HISTORICAL closes while the fills happened
# at today's price. It is now refused: 400 on a non-today as_of_date for any
# armed strategy, 409 (pointing at the autopilot Run-now) for a fund member.
# ===========================================================================
def test_F3_manual_run_now_with_stale_as_of_over_deploys_the_sleeve(client, user, monkeypatch):
    monkeypatch.setattr(demo_fills, "live_price", lambda account, ticker: Decimal("200"))
    fund, s = _fund(
        user, {"A": 100},
        kind=PortfolioStrategy.KIND_RISK_PARITY,
        target_gross_pct=Decimal("1.00"), per_etf_max_pct=Decimal("0.50"),
        per_etf_min_pct=Decimal("0.02"), max_position_pct=Decimal("0.50"),
        max_turnover_pct=Decimal("5.0"), personas=[],
    )
    strategy = s["A"]

    dispatched: list = []

    def _delay(*a, **k):
        dispatched.append((a, k))
        return types.SimpleNamespace(id="task-1")

    monkeypatch.setattr(tasks.daily_long_short_cycle, "delay", _delay)

    # A caller-supplied historical as_of_date is refused outright.
    r = client.post(
        f"/api/strategies/{strategy.id}/run-now/", {"as_of_date": "2024-01-05"}, format="json",
    )
    assert r.status_code == 400, r.json()
    assert "today" in r.json()["detail"]

    # And so is a same-day run: an armed fund member goes through the
    # autopilot's own Run-now, which audits + caps + pre-flight checks it.
    r = client.post(f"/api/strategies/{strategy.id}/run-now/", {}, format="json")
    assert r.status_code == 409, r.json()
    assert r.json()["autopilot_run_now"] == f"/api/strategies/{strategy.id}/autopilot/run-now/"

    assert dispatched == []
    assert PortfolioTarget.objects.count() == 0
    assert BrokerOrder.objects.count() == 0
    assert AutopilotRun.objects.count() == 0

    # A malformed date is a 400, not a 500.
    r = client.post(
        f"/api/strategies/{strategy.id}/run-now/", {"as_of_date": "not-a-date"}, format="json",
    )
    assert r.status_code == 400, r.json()

    # The non-armed path is unchanged: it still queues.
    StrategyAutopilot.objects.filter(strategy=strategy).update(is_enabled=False)
    r = client.post(f"/api/strategies/{strategy.id}/run-now/", {}, format="json")
    assert r.status_code == 202, r.json()
    assert len(dispatched) == 1


# ===========================================================================
# F4 (FIXED) — audit completeness: any exception inside the cycle now marks the
# AutopilotRun *and* the in-flight PortfolioTarget `failed` with the error text
# and pages the operator. The exception still propagates so Celery records it.
# ===========================================================================
def test_F4_cycle_exception_leaves_autopilot_run_stuck_running_without_error(
    user, price_200, monkeypatch,
):
    fund, s = _fund(user, {"A": 100})
    ap = s["A"].autopilot
    run = AutopilotRun.objects.create(autopilot=ap, fire_time_utc=timezone.now())
    target = PortfolioTarget.objects.create(
        strategy=s["A"], as_of_date=dt.date(2026, 9, 4),
        status=PortfolioTarget.RUNNING_COUNCIL,
    )

    def _boom(*a, **k):
        raise RuntimeError("FMP 429 / Universe has no active members / anything")

    alerts: list[tuple] = []
    monkeypatch.setattr(tasks, "daily_long_short_cycle", _boom)
    monkeypatch.setattr(
        "apps.notifications.autopilot.notify_autopilot",
        lambda ap_, event, summary, **kw: alerts.append((event, summary)),
    )
    with pytest.raises(RuntimeError):
        tasks_autopilot.run_autopilot_cycle(run.id)
    run.refresh_from_db()
    assert run.status == AutopilotRun.FAILED
    assert "FMP 429" in run.error
    assert run.finished_at is not None
    target.refresh_from_db()
    assert target.status == PortfolioTarget.FAILED
    assert "FMP 429" in target.error_message
    assert alerts and "FAILED" in alerts[0][1]


# ===========================================================================
# F5 (FIXED) — the §9 gate was enable-time only: editing the strategy after
# enabling re-locked the toggle (validation.passed=False) but the autopilot
# stayed enabled and kept trading the edited, never-backtested config. A
# risk-field edit now auto-disables it with reason `config_changed`.
# ===========================================================================
def test_F5_config_edit_after_enable_keeps_trading_unvalidated(client, user, price_200):
    fund, s = _fund(user, {"A": 100}, enable=False)
    strategy = s["A"]
    _gate_grade_backtest(strategy)
    r = client.post(f"/api/strategies/{strategy.id}/autopilot/enable/")
    assert r.status_code == 200, r.json()
    assert r.json()["autopilot"]["validation"]["passed"] is True

    # Edit the risk config through the normal strategy endpoint.
    r = client.patch(
        f"/api/strategies/{strategy.id}/",
        {"target_gross_pct": "3.00", "max_position_pct": "0.99"}, format="json",
    )
    assert r.status_code == 200, r.json()
    body = client.get(f"/api/strategies/{strategy.id}/autopilot/").json()["autopilot"]
    assert body["validation"]["passed"] is False          # gate says: not validated
    assert body["is_enabled"] is False                    # ...so it is disarmed too
    assert body["next_run_at"] is None

    # The audit says why.
    run = AutopilotRun.objects.filter(autopilot__strategy=strategy).latest("fire_time_utc")
    assert run.submit_decision["disabled"] == "config_changed"
    assert "max_position_pct" in run.submit_decision["fields"]

    # And nothing trades the new config: the terminal hook is a no-op now.
    strategy.refresh_from_db()
    assert strategy.max_position_pct == Decimal("0.9900")
    assert bridge._finalize_target(_done_target(strategy, {"AAPL": 0.60})) is None
    assert BrokerOrder.objects.count() == 0


# ===========================================================================
# F6 — the fund-level drawdown breaker is NOT flow-adjusted: a planned capital
# withdrawal from the shared paper account (the ±$25k "capital tilt" the
# snapshot layer explicitly books as an external flow) reads as a drawdown and
# halts every member.
# ===========================================================================
def test_F6_fund_breaker_treats_a_withdrawal_as_a_drawdown(user, price_200):
    fund, s = _fund(user, {"A": 50, "B": 50})
    assert fund.peak_equity_usd == Decimal("100000.00")
    acct_pf = fund.broker_account.portfolio
    # A $25k withdrawal squared by reconcile (a reconciliation cash adjustment).
    from apps.portfolios.models import LedgerEntry

    acct_pf.cash_balance = Decimal("75000.00")
    acct_pf.save(update_fields=["cash_balance"])
    LedgerEntry.objects.create(
        portfolio=acct_pf, kind=LedgerEntry.KIND_RECONCILE, cash_delta=Decimal("-25000"),
        cash_balance_after=Decimal("75000.00"), note="reconciliation_adjustment (cash)",
    )
    res = fund_layer.evaluate_fund_drawdown(fund)
    assert res["drawdown_pct"] == 25.0
    assert res["halted"] is True
    assert StrategyAutopilot.objects.filter(state=StrategyAutopilot.STATE_HALTED).count() == 2
