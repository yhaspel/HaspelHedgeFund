"""P14-B — fitting a sleeve's order to what the venue lets ONE account do.

Two sleeves share the fund's Alpaca account and can legitimately sit on opposite
sides of the same name, so a sleeve's order can be — at the ACCOUNT level — a
position flip or a fractional short, both of which Alpaca refuses in one shot.
``autopilot.venue_fit`` clamps a crossing order to account-flat and floors any
quantity that leaves the account short to whole shares, before the gate on both
credentialed submission paths.

Covers: the truth table, the AutopilotRun audit trail, both submission paths,
the untouched demo path, and the 2026-09-08 next-open scenario.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.brokers import demo_fills, idempotency, reconcile
from apps.brokers.adapters import mock as mock_adapter
from apps.brokers.models import BrokerAccount, BrokerOrder
from apps.portfolios import autopilot as bridge
from apps.portfolios import sleeves, tasks_autopilot
from apps.portfolios.models import (
    AutonomousFund,
    AutopilotRun,
    Portfolio,
    PortfolioStrategy,
    Position,
    StrategyAutopilot,
    Universe,
    UniverseMembership,
)

User = get_user_model()

# The live 2026-09-08 book: Cross-Asset Risk Parity's fills on the shared account.
RP_FILLS = {
    "TLT": Decimal("63.800071"),
    "TIP": Decimal("109.525681"),
    "IEF": Decimal("111.549718"),
    "GLD": Decimal("4.979714"),
}
# Cross-Asset Trend's held sells for the same four names.
TREND_SELLS = {
    "TLT": Decimal("66"), "TIP": Decimal("101"),
    "IEF": Decimal("116"), "GLD": Decimal("4"),
}


@pytest.fixture(autouse=True)
def _reset_mock():
    mock_adapter.reset_state()
    yield
    mock_adapter.reset_state()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="p14b@x.test", password="pw-fake-123456789")


def _strategy(user, name="S", **kw):
    u = Universe.objects.create(name=f"p14b-uni-{name}")
    for t in ("TLT", "TIP", "IEF"):
        UniverseMembership.objects.create(
            universe=u, ticker=t, sector="Bonds", effective_from=dt.date(2020, 1, 1),
        )
    pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_STRATEGY, name=name)
    defaults = dict(
        kind=PortfolioStrategy.KIND_LONG_SHORT,
        max_position_pct=Decimal("0.50"),
        max_sector_pct=Decimal("0.90"),
        min_trade_notional_usd=Decimal("100"),
        max_turnover_pct=Decimal("1.0"),
        auto_run_council=True,
    )
    defaults.update(kw)
    return PortfolioStrategy.objects.create(
        user=user, name=name, universe=u, portfolio=pf, **defaults,
    )


def _account(user, *, broker="alpaca_paper", cash="100000", label="POOL"):
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


def _hold(account, ticker, quantity):
    """Put a signed position on the ACCOUNT's book (the shared pool)."""
    return Position.objects.create(
        portfolio=account.portfolio, ticker=ticker, quantity=Decimal(quantity),
        avg_cost=Decimal("100"),
    )


def _order(account, ticker, side, quantity, **kw):
    return BrokerOrder.objects.create(
        broker_account=account, ticker=ticker, side=side,
        quantity=Decimal(quantity), order_type="market", **kw,
    )


def _run(strategy, *, minutes_ago=1):
    ap, _ = StrategyAutopilot.objects.get_or_create(strategy=strategy)
    return AutopilotRun.objects.create(
        autopilot=ap, fire_time_utc=timezone.now() - dt.timedelta(minutes=minutes_ago),
        status=AutopilotRun.PENDING,
    )


@pytest.fixture
def submissions(monkeypatch):
    """Record what reaches the venue instead of talking to Alpaca. Both names are
    imported *inside* the submit functions, so patching the module attribute works."""
    seen: list[dict] = []

    def _recorder(*, order, broker, **kw):
        seen.append({
            "id": order.pk, "ticker": order.ticker,
            "quantity": Decimal(str(order.quantity)),
        })

    monkeypatch.setattr(idempotency, "submit_idempotent", _recorder)
    monkeypatch.setattr(reconcile, "get_broker", lambda account: SimpleNamespace(name="stub"))
    return seen


# --------------------------------------------------------------------------
# 1. The truth table.
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("held", "side", "requested", "expected", "changed"),
    [
        # A cross through zero is clamped to the quantity that takes the account flat.
        ("63.800071", "sell", "66", "63.800071", True),
        # Reductions inside a long pass through untouched.
        ("109.53", "sell", "101", "101", False),
        ("4.98", "sell", "4", "4", False),
        # Opening a whole-share short from flat is fine; a fractional one is floored.
        ("0", "sell", "66", "66", False),
        ("0", "sell", "1.3", "1", True),
        ("0", "sell", "0.7", None, True),  # residual < 1 share -> rejected locally
        # Covering: clamp at flat, floor anything that leaves the account short.
        ("-2", "buy", "5", "2", True),
        ("-66", "buy", "2.5", "2", True),
        ("-66", "buy", "66", "66", False),
        # Same-side adds inside a long are never touched.
        ("5", "buy", "10", "10", False),
        ("5", "buy", "0.4", "0.4", False),
    ],
)
def test_venue_fit_truth_table(user, held, side, requested, expected, changed):
    account = _account(user)
    if Decimal(held) != 0:
        _hold(account, "TLT", held)
    order = _order(account, "TLT", side, requested)

    record = bridge.venue_fit(order)

    if not changed:
        assert record is None
        order.refresh_from_db()
        assert order.quantity == Decimal(requested)
        assert order.status == BrokerOrder.STATUS_DRAFT
        return

    assert record is not None
    assert Decimal(record["held"]) == Decimal(held)
    assert Decimal(record["requested"]) == Decimal(requested)
    assert record["notes"]
    order.refresh_from_db()
    if expected is None:
        assert record["rejected"] is True
        assert order.status == BrokerOrder.STATUS_REJECTED
        assert order.error_message.startswith("venue fit:")
    else:
        assert "rejected" not in record
        assert Decimal(record["quantity"]) == Decimal(expected)
        assert order.quantity == Decimal(expected)
        assert order.status == BrokerOrder.STATUS_DRAFT


def test_venue_fit_ignores_an_account_without_a_book(user):
    """Last row of the table: no account book -> nothing to reconcile against."""
    order = SimpleNamespace(
        pk=1, broker_account=SimpleNamespace(), ticker="TLT", side="sell",
        quantity=Decimal("66"),
    )
    assert bridge.venue_fit(order) is None
    assert order.quantity == Decimal("66")


# --------------------------------------------------------------------------
# 2. Audit trail on the linked AutopilotRun.
# --------------------------------------------------------------------------
def test_venue_fit_records_on_the_linked_run(user):
    account = _account(user)
    _hold(account, "TLT", "63.800071")
    _hold(account, "IEF", "111.549718")
    run = _run(_strategy(user, name="TREND"))

    o1 = _order(account, "TLT", "sell", "66")
    run.broker_orders.add(o1)
    assert bridge.venue_fit(o1) is not None
    run.refresh_from_db()
    fits = run.guardrail_actions["account_venue_fit"]
    assert len(fits) == 1
    assert fits[0]["ticker"] == "TLT" and fits[0]["quantity"] == "63.800071"

    o2 = _order(account, "IEF", "sell", "116")
    run.broker_orders.add(o2)
    assert bridge.venue_fit(o2) is not None
    run.refresh_from_db()
    fits = run.guardrail_actions["account_venue_fit"]
    assert [f["ticker"] for f in fits] == ["TLT", "IEF"]  # appends, never replaces

    # An order with no run only logs — no crash, no record anywhere.
    o3 = _order(account, "TLT", "sell", "66")
    assert bridge.venue_fit(o3) is not None
    run.refresh_from_db()
    assert len(run.guardrail_actions["account_venue_fit"]) == 2


# --------------------------------------------------------------------------
# 3. The release path (§6.6) fits before the gate.
# --------------------------------------------------------------------------
def test_submit_held_order_fits_before_gate(user, submissions):
    account = _account(user)
    _hold(account, "TLT", "63.800071")
    order = _order(
        account, "TLT", "sell", "66",
        status=BrokerOrder.STATUS_PENDING_OPEN,
        release_after=timezone.now() - dt.timedelta(minutes=1),
    )

    assert bridge.submit_held_order(order) is True

    assert [s["quantity"] for s in submissions] == [Decimal("63.800071")]
    order.refresh_from_db()
    assert order.quantity == Decimal("63.800071")
    assert order.status != BrokerOrder.STATUS_PENDING_OPEN
    # The gate saw the FITTED quantity: 63.800071 x the $100 deterministic quote.
    assert order.confirmation_audit["notional"] == "6380.01"


def test_submit_held_order_rejects_a_sub_share_residual(user, submissions):
    account = _account(user)  # flat book
    order = _order(
        account, "TLT", "sell", "0.7",
        status=BrokerOrder.STATUS_PENDING_OPEN,
        release_after=timezone.now() - dt.timedelta(minutes=1),
    )

    assert bridge.submit_held_order(order) is False

    assert submissions == []
    order.refresh_from_db()
    assert order.status == BrokerOrder.STATUS_REJECTED
    assert order.error_message.startswith("venue fit:")


# --------------------------------------------------------------------------
# 4. The immediate path.
# --------------------------------------------------------------------------
def _emit(account, user, ticker, side, quantity, coid):
    return bridge._emit_one(
        account=account, user=user, client_order_id=coid, ticker=ticker,
        broker_side=side, quantity=Decimal(quantity), is_demo=False,
        market_closed=False, risk_check=lambda o: [], next_open_fn=timezone.now,
    )


def test_emit_one_fits_on_the_immediate_path(user, submissions):
    account = _account(user)
    _hold(account, "TLT", "63.800071")

    order = _emit(account, user, "TLT", "sell", "66", "p14b-1")

    assert [s["quantity"] for s in submissions] == [Decimal("63.800071")]
    order.refresh_from_db()
    assert order.quantity == Decimal("63.800071")
    assert order.confirmation_audit["notional"] == "6380.01"


def test_emit_one_raises_risk_rejected_when_nothing_is_left(user, submissions):
    account = _account(user)  # flat book

    with pytest.raises(bridge._RiskRejected) as exc:
        _emit(account, user, "TLT", "sell", "0.7", "p14b-2")

    assert str(exc.value).startswith("venue fit:")
    assert submissions == []
    order = BrokerOrder.objects.get(client_order_id="p14b-2")
    assert order.status == BrokerOrder.STATUS_REJECTED


# --------------------------------------------------------------------------
# 5. The demo simulator is untouched — credentialed venues only.
# --------------------------------------------------------------------------
def test_demo_path_is_unchanged(user, monkeypatch):
    monkeypatch.setattr(demo_fills, "live_price", lambda account, ticker: Decimal("100"))
    account = _account(user, broker="mock")
    _hold(account, "TLT", "5")  # the demo book holds +5; the order crosses zero
    run = _run(_strategy(user, name="DEMO"))
    order = _order(
        account, "TLT", "sell", "10",
        status=BrokerOrder.STATUS_PENDING_OPEN,
        release_after=timezone.now() - dt.timedelta(minutes=1),
    )
    run.broker_orders.add(order)

    assert bridge.submit_held_order(order) is True

    order.refresh_from_db()
    assert order.quantity == Decimal("10")  # the simulator handles the flip
    run.refresh_from_db()
    assert "account_venue_fit" not in run.guardrail_actions


# --------------------------------------------------------------------------
# 6. The real next open: two sleeves, opposite sides, one account.
# --------------------------------------------------------------------------
def test_next_open_scenario_two_sleeves_opposite_sides(user, monkeypatch, submissions):
    account = _account(user)
    fund = AutonomousFund.objects.create(owner=user, name="Autonomous Fund")
    sleeves.configure_account(fund, account)
    rp, trend = _strategy(user, name="RP"), _strategy(user, name="TREND")
    for s in (rp, trend):
        StrategyAutopilot.objects.get_or_create(strategy=s)
    sleeves.set_members(fund, [
        {"strategy_id": rp.id, "allocation_pct": "50"},
        {"strategy_id": trend.id, "allocation_pct": "50"},
    ])
    sleeves.reset_fund(fund)

    # RP fired first: its four buys are filled and on the ACCOUNT's book.
    run_rp = _run(rp, minutes_ago=2)
    for ticker, qty in RP_FILLS.items():
        _hold(account, ticker, qty)
        filled = _order(
            account, ticker, "buy", qty,
            status=BrokerOrder.STATUS_FILLED, filled_quantity=qty,
            sleeve=sleeves.sleeve_for(rp),
        )
        run_rp.broker_orders.add(filled)

    # Trend fired a tick later: its four sells are still held for the open.
    run_trend = _run(trend, minutes_ago=1)
    past = timezone.now() - dt.timedelta(minutes=1)
    for ticker, qty in TREND_SELLS.items():
        held = _order(
            account, ticker, "sell", qty,
            status=BrokerOrder.STATUS_PENDING_OPEN, release_after=past,
            sleeve=sleeves.sleeve_for(trend),
        )
        run_trend.broker_orders.add(held)

    monkeypatch.setattr("apps.brokers.market_calendar.is_market_open", lambda *a, **k: True)
    res = tasks_autopilot.release_pending_open_orders()

    assert res == {"released": 4, "candidates": 4, "deferred": 0}
    assert {s["ticker"]: s["quantity"] for s in submissions} == {
        "TLT": Decimal("63.800071"),   # clamped: RP is long 63.800071, trend sells 66
        "TIP": Decimal("101"),         # untouched: still long 8.525681 after
        "IEF": Decimal("111.549718"),  # clamped: RP is long 111.549718, trend sells 116
        "GLD": Decimal("4"),           # untouched: still long 0.979714 after
    }
    run_trend.refresh_from_db()
    assert [f["ticker"] for f in run_trend.guardrail_actions["account_venue_fit"]] == ["TLT", "IEF"]
    run_rp.refresh_from_db()
    assert "account_venue_fit" not in run_rp.guardrail_actions
