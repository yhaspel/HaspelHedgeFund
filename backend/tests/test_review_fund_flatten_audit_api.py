"""Adversarial review (reviewer: fund) — batch 2: flatten vs in-flight cycle
orders, audit completeness of AutopilotRun, autopilot/fund API validation,
breaker/gate inconsistencies, turnover-cap semantics.

Run:
  DJANGO_SETTINGS_MODULE=hedgefund.settings.test /tmp/v312/bin/pytest \
      tests/test_review_fund_flatten_audit_api.py -q -p no:cacheprovider
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
from apps.portfolios import autopilot_risk, sleeves, tasks_autopilot
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
from apps.portfolios.rebalance import CurrentPosition, RebalanceConfig, compute_orders
from apps.portfolios.validation import validation_status

User = get_user_model()


@pytest.fixture(autouse=True)
def _reset_mock():
    mock_adapter.reset_state()
    yield
    mock_adapter.reset_state()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="rev-fund2@x.test", password="pw-fake-123456789")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    c.raise_request_exception = False
    return c


@pytest.fixture
def price_200(monkeypatch):
    monkeypatch.setattr(demo_fills, "live_price", lambda account, ticker: Decimal("200"))


def _strategy(user, name="S", **kw):
    u = Universe.objects.create(name=f"rev2-uni-{name}")
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
    return bridge.maybe_emit_and_submit(
        _done_target(strategy, weights, **kw), autopilot=strategy.autopilot,
    )


def _positions(book):
    return {p.ticker: Decimal(str(p.quantity)) for p in book.positions.all()}


# ===========================================================================
# F7 (FIXED) — Flatten used to ignore the cycle's own in-flight (pending_open)
# orders: it only skipped names with a pending `flat-` order. With the market
# closed the Friday batch was still held; "Flatten" reported 0 orders, the
# account looked flat, and at the open the held batch deployed the book anyway.
# Flatten now CANCELS the held batch first (with an audit note).
# ===========================================================================
def test_F7_flatten_does_not_cancel_pending_cycle_orders(user, price_200, monkeypatch):
    fund, s = _fund(user, {"A": 100}, broker="alpaca_paper")
    acct = fund.broker_account
    monkeypatch.setattr("apps.brokers.market_calendar.is_market_open", lambda *a, **k: False)
    dec = _cycle(s["A"], {"AAPL": 0.10})
    assert dec["pending_open"] == 1
    assert sleeves.reset_readiness(fund)["reason"] == "inflight_orders"
    held = BrokerOrder.objects.get(broker_account=acct)

    res = sleeves.flatten_fund(fund, reason="manual_flatten")
    assert res["orders"] == 0 and res["skipped_inflight"] == []    # no positions yet
    assert [c["order_id"] for c in res["cancelled_pending_open"]] == [held.id]

    # The held cycle order can no longer buy at the open.
    held.refresh_from_db()
    assert held.status == BrokerOrder.STATUS_CANCELLED
    assert held.release_after is None
    assert "fund flatten" in held.error_message
    assert sleeves.reset_readiness(fund)["ready"] is True          # flat + nothing in flight

    # The open arrives (re-point at the demo broker so a release would fill).
    BrokerAccount.objects.filter(pk=acct.pk).update(broker="mock")
    mock_adapter.seed_demo_book(acct, cash=Decimal("100000"))
    monkeypatch.setattr("apps.brokers.market_calendar.is_market_open", lambda *a, **k: True)
    assert tasks_autopilot.release_pending_open_orders()["released"] == 0
    acct.portfolio.refresh_from_db()
    assert _positions(acct.portfolio) == {}                        # stays flat


# ===========================================================================
# F8 (FIXED) — release-time outcomes now reach the AutopilotRun audit: an order
# held pending_open and REJECTED at the open (risk gate / venue fit) used to
# leave the run saying "submitted, 1 held for open" forever.
# ``autopilot_audit.record_release_outcome`` writes the outcome back (with the
# shadow daily-cap evaluation alongside it).
# ===========================================================================
def test_F8_release_time_rejection_is_invisible_in_the_run_audit(
    client, user, price_200, monkeypatch,
):
    fund, s = _fund(user, {"A": 100}, broker="alpaca_paper")
    acct = fund.broker_account
    ap = s["A"].autopilot
    run = AutopilotRun.objects.create(
        autopilot=ap, fire_time_utc=timezone.now(), status=AutopilotRun.RUNNING,
    )
    monkeypatch.setattr("apps.brokers.market_calendar.is_market_open", lambda *a, **k: False)
    _cycle(s["A"], {"AAPL": 0.10})
    run.refresh_from_db()
    assert run.status == AutopilotRun.SUBMITTED and run.submit_decision["pending_open"] == 1
    held = BrokerOrder.objects.get(broker_account=acct)

    # At the open the risk gate rejects it (e.g. the sleeve cap was tightened).
    monkeypatch.setattr(autopilot_risk, "make_risk_check", lambda *a, **k: (lambda o: ["cap"]))
    BrokerOrder.objects.filter(pk=held.pk).update(
        release_after=timezone.now() - dt.timedelta(minutes=1),
    )
    monkeypatch.setattr("apps.brokers.market_calendar.is_market_open", lambda *a, **k: True)
    out = tasks_autopilot.release_pending_open_orders()
    assert out["released"] == 0
    held.refresh_from_db()
    assert held.status == BrokerOrder.STATUS_REJECTED

    # The run now records the release outcome.
    run.refresh_from_db()
    assert run.submit_decision["pending_open"] == 0
    assert run.submit_decision["items"][0]["status"] == "rejected"
    release = run.submit_decision["release"][-1]
    assert release["failed"] == [held.id] and release["released"] == []
    assert release["caps_shadow"]["shadow"] is True
    body = client.get(f"/api/strategies/{s['A'].id}/autopilot/history/").json()["runs"][0]
    assert body["n_orders"] == 1
    assert "rejected" in str(body["submit_decision"])


# ===========================================================================
# F9 — dd_hard_halt_pct = 0 (or negative) silently DISABLES the live breaker
# (`hard > 0` guard) while the §9 gate keeps validating backtests against the
# 7.5% default — the two halves disagree about what the limit is.
# ===========================================================================
def test_F9_zero_hard_halt_disables_the_breaker_but_gate_pretends_7_5(user, price_200, monkeypatch):
    from apps.backtests.models import Backtest, BacktestMetrics

    fund, s = _fund(user, {"A": 100})
    ap = s["A"].autopilot
    ap.dd_hard_halt_pct = Decimal("0")
    ap.peak_equity_usd = Decimal("100000")
    ap.save()
    monkeypatch.setattr(autopilot_risk, "broker_equity", lambda a: Decimal("40000"))   # −60%
    out = autopilot_risk.evaluate_drawdown(ap)
    assert out["drawdown_pct"] == 60.0
    assert out["state"] == StrategyAutopilot.STATE_SOFT_CUT           # never halts
    bt = Backtest.objects.create(
        user=user, strategy=s["A"], name="bt",
        start_date=dt.date(2024, 1, 1), end_date=dt.date(2025, 1, 1), status=Backtest.DONE,
    )
    BacktestMetrics.objects.create(
        backtest=bt, mean_oos_sharpe=Decimal("0.8"), max_drawdown_pct=Decimal("7.0"),
        sharpe=Decimal("0.7"),
    )
    checks = {c["key"]: c for c in validation_status(s["A"])["checks"]}
    assert "limit 7.5%" in checks["drawdown_within_limit"]["detail"]   # gate: 7.5% "limit"


# ===========================================================================
# F10 (partly FIXED) — autopilot PUT: unvalidated fields 500'd instead of
# 400'ing, and nonsense values that did not 500 were persisted (negative
# hard-halt, invalid choice strings). The autopilot PUT now validates them.
# The fund PUT / members PUT / run-now paths are covered separately.
# ===========================================================================
@pytest.mark.parametrize("body", [
    {"timezone": "Mars/Olympus"},
    {"max_orders_per_day": "abc"},
    {"max_orders_per_day": -5},
    {"max_orders_per_day": None},
    {"target_vol_pct": "NaN"},
    {"target_vol_pct": "Infinity"},
    {"target_vol_pct": "0"},
    {"target_vol_pct": None},
    {"dd_hard_halt_pct": ""},
    {"max_notional_per_day_usd": "-1"},
])
def test_F10a_autopilot_put_500s_on_bad_input(client, user, body):
    s = _strategy(user)
    StrategyAutopilot.objects.create(strategy=s, is_enabled=True)
    r = client.put(f"/api/strategies/{s.id}/autopilot/", body, format="json")
    assert r.status_code == 400, (body, r.status_code)


def test_F10b_autopilot_put_persists_nonsense(client, user):
    s = _strategy(user)
    StrategyAutopilot.objects.create(strategy=s, is_enabled=True)
    before = StrategyAutopilot.objects.get(strategy=s)
    r = client.put(
        f"/api/strategies/{s.id}/autopilot/",
        {"dd_hard_halt_pct": "-3", "dd_soft_cut_pct": "50", "short_mode": "whatever"},
        format="json",
    )
    assert r.status_code == 400, r.json()
    ap = StrategyAutopilot.objects.get(strategy=s)
    assert ap.dd_hard_halt_pct == before.dd_hard_halt_pct   # nothing half-applied
    assert ap.short_mode == before.short_mode

    # soft ≥ hard is refused too (the breaker would never soft-cut).
    r = client.put(
        f"/api/strategies/{s.id}/autopilot/",
        {"dd_soft_cut_pct": "9", "dd_hard_halt_pct": "7.5"}, format="json",
    )
    assert r.status_code == 400 and "below" in r.json()["detail"]

    # A sane edit still applies, and 0 orders/day is accepted and MEANS zero.
    r = client.put(
        f"/api/strategies/{s.id}/autopilot/",
        {"dd_soft_cut_pct": "4", "dd_hard_halt_pct": "8", "max_orders_per_day": 0,
         "short_mode": StrategyAutopilot.SHORT_CASH},
        format="json",
    )
    assert r.status_code == 200, r.json()
    ap.refresh_from_db()
    assert (ap.dd_soft_cut_pct, ap.dd_hard_halt_pct) == (Decimal("4.00"), Decimal("8.00"))
    assert ap.max_orders_per_day == 0 and ap.short_mode == StrategyAutopilot.SHORT_CASH


@pytest.mark.parametrize("path,body", [
    ("/api/fund/", {"fund_dd_halt_pct": "NaN"}),
    ("/api/fund/", {"broker_account_id": "abc"}),
])
def test_F10c_fund_put_500s_on_bad_input(client, user, path, body):
    r = client.put(path, body, format="json")
    assert r.status_code == 500, (body, r.status_code)


def test_F10d_members_nan_allocation_500s(client, user):
    fund = AutonomousFund.objects.create(owner=user, name="F")
    s = _strategy(user)
    r = client.put(
        "/api/fund/members/",
        {"members": [{"strategy_id": s.id, "allocation_pct": "NaN"}]}, format="json",
    )
    assert r.status_code == 500
    assert fund.sleeves.count() == 0


def test_F10e_run_now_bad_as_of_date_500s(client, user):
    """FIXED: a malformed as_of_date is a 400, not a 500."""
    s = _strategy(user)
    r = client.post(f"/api/strategies/{s.id}/run-now/", {"as_of_date": "not-a-date"}, format="json")
    assert r.status_code == 400, r.status_code
    assert "as_of_date" in r.json()["detail"]


# ===========================================================================
# F11 — a due autopilot whose sibling has an invalid timezone stalls the whole
# dispatcher: compute_next() raises before the advance is saved, so the row
# stays due forever and every later beat tick dies on it (rows after it in the
# id order never dispatch).
# ===========================================================================
def test_F11_bad_timezone_row_stalls_dispatch_for_later_autopilots(user, price_200, monkeypatch):
    fund, s = _fund(user, {"A": 50, "B": 50})
    a, b = s["A"].autopilot, s["B"].autopilot
    StrategyAutopilot.objects.filter(pk=a.pk).update(
        timezone="Mars/Olympus", is_market_aware=False,
        next_run_at=timezone.now() - dt.timedelta(minutes=1),
    )
    StrategyAutopilot.objects.filter(pk=b.pk).update(
        is_market_aware=False, next_run_at=timezone.now() - dt.timedelta(minutes=1),
    )
    fired: list[int] = []
    monkeypatch.setattr(tasks_autopilot.run_autopilot_cycle, "delay", lambda rid: fired.append(rid))
    with pytest.raises(Exception):  # noqa: B017 — the point IS the unhandled crash
        tasks_autopilot.dispatch_due_autopilots()
    assert fired == []                                   # B (healthy, due) never dispatched
    a.refresh_from_db()
    assert a.next_run_at < timezone.now()                # A is still due → same crash next tick
    # Note: the invalid tz can only be stored on a DISABLED autopilot via PUT
    # (reschedule() raises on an enabled one) — enabling later then 500s; a
    # DB-level edit / migration / admin edit reaches this state directly.


# ===========================================================================
# F12 — max_turnover_pct semantics (undocumented, hidden from the UI, absent
# from the backtest): it caps the ADDED exposure per cycle at
# max_turnover_pct × NAV; risk-off legs are exempt. With the model default
# 0.30 a from-cash deployment takes ≥ 4 weekly cycles to reach a 1.0 gross;
# with 1.0 a levered book (rp_max_gross > 1) can never exceed 1× gross in one
# cycle. The validated backtest deploys the full target every period.
# ===========================================================================
def test_F12_turnover_cap_throttles_from_cash_deploy_and_leverage():
    cfg = dict(last_close={"A": 100.0, "B": 100.0}, min_trade_notional_usd=0.0)
    # Model default 0.30: only 30% of NAV is deployed on the first cycle.
    orders = compute_orders(
        [], {"A": 0.5, "B": 0.5},
        RebalanceConfig(portfolio_value=100_000, max_turnover_pct=0.30, **cfg),
    )
    assert sum(o.estimated_notional_usd for o in orders) == pytest.approx(30_000)
    # 1.0: a full 1× gross deploys, but a 1.5× levered RP book is clipped to 1×.
    orders = compute_orders(
        [], {"A": 0.75, "B": 0.75},
        RebalanceConfig(portfolio_value=100_000, max_turnover_pct=1.0, **cfg),
    )
    assert sum(o.estimated_notional_usd for o in orders) == pytest.approx(100_000)
    # Risk-off legs are exempt: a full close runs even at a 0.0-ish cap (>0 guard).
    cur = [CurrentPosition(ticker="A", quantity=1000, avg_cost=100.0)]
    orders = compute_orders(
        cur, {}, RebalanceConfig(portfolio_value=100_000, max_turnover_pct=0.01, **cfg),
    )
    assert [o.reason for o in orders] == ["close"] and orders[0].quantity == 1000


# ===========================================================================
# F13 — the autopilot's cost_ceiling_usd / on_breach are dead configuration:
# stored + exposed by the API/UI, never read by any autopilot task (only the
# schedules app reads ITS OWN copies). Proof by import-time grep.
# ===========================================================================
def test_F13_autopilot_cost_ceiling_and_on_breach_are_never_read():
    import inspect
    import re

    from apps.portfolios import autopilot as ap_mod
    from apps.portfolios import tasks as tasks_mod
    from apps.portfolios import tasks_autopilot as ta_mod

    src = "".join(inspect.getsource(m) for m in (ap_mod, tasks_mod, ta_mod))
    # No attribute read of the autopilot's own ceiling / breach policy anywhere
    # on the execution path (the only hit is the STRATEGY's per-cycle ceiling).
    assert re.search(r"\.(cost_ceiling_usd|on_breach)\b", src) is None
    assert "cost_ceiling_per_cycle_usd" in src
