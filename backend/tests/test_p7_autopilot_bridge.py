"""P7 Stage A — the autonomous strategy→broker bridge, risk gate, idempotency,
scheduler, and the byte-identical no-autopilot regression."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

from apps.brokers import demo_fills
from apps.brokers.adapters import mock as mock_adapter
from apps.brokers.confirmation import ConfirmationError, GateContext, gate
from apps.brokers.interfaces import BrokerAuthError
from apps.brokers.models import BrokerAccount, BrokerOrder, StrategyBrokerLink
from apps.portfolios import autopilot as bridge
from apps.portfolios import autopilot_risk, tasks_autopilot
from apps.portfolios.models import (
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
    return User.objects.create_user(email="p7@x.test", password="pw-fake-123456789")


def _universe():
    u = Universe.objects.create(name="p7-uni")
    for t in ("AAPL", "MSFT"):
        UniverseMembership.objects.create(
            universe=u,
            ticker=t,
            sector="Tech",
            effective_from=dt.date(2020, 1, 1),
        )
    return u


def _strategy(user, **kw):
    pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_STRATEGY, name="sbook")
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
        user=user,
        name="S",
        universe=_universe(),
        portfolio=pf,
        **defaults,
    )


def _broker_account(
    user,
    *,
    broker="alpaca_paper",
    mode=BrokerAccount.MODE_PAPER,
    cash="100000",
    active=True,
):
    pf = Portfolio.objects.create(
        user=user,
        kind=Portfolio.KIND_BROKER,
        name="bk",
        cash_balance=Decimal(cash),
    )
    acc = BrokerAccount.objects.create(
        user=user,
        broker=broker,
        mode=mode,
        account_id=f"{broker}-{user.id}",
        label="L",
        portfolio=pf,
        connection_status=(
            BrokerAccount.STATUS_ACTIVE if active else BrokerAccount.STATUS_NEEDS_REAUTH
        ),
    )
    if broker == "mock":
        mock_adapter.seed_demo_book(acc, cash=Decimal(cash))
    return acc


def _linked(user, account, *, enabled=True, state=StrategyAutopilot.STATE_ACTIVE, **skw):
    strategy = _strategy(user, **skw)
    StrategyBrokerLink.objects.create(strategy=strategy, broker_account=account)
    ap = StrategyAutopilot.objects.create(
        strategy=strategy,
        broker_account=account,
        is_enabled=enabled,
        state=state,
    )
    return strategy, ap


def _done_target(strategy, weights):
    target = PortfolioTarget.objects.create(
        strategy=strategy,
        as_of_date=dt.date(2026, 6, 4),
        status=PortfolioTarget.DONE,
        target_weights=weights,
    )
    # Finalize-style RebalanceOrders (price seed for the broker-book recompute).
    for t in weights:
        RebalanceOrder.objects.create(
            target=target,
            ticker=t,
            side="buy",
            quantity=Decimal("1"),
            limit_price=Decimal("200"),
            reason="open",
            estimated_notional_usd=Decimal("200"),
            sequence=2,
        )
    return target


# --------------------------------------------------------------------------
# Bridge: demo fill path (orders submit + fill into the broker book).
# --------------------------------------------------------------------------
def test_bridge_demo_fill_creates_and_submits(user, monkeypatch):
    monkeypatch.setattr(demo_fills, "live_price", lambda account, ticker: Decimal("200"))
    account = _broker_account(user, broker="mock")
    strategy, ap = _linked(user, account)
    target = _done_target(strategy, {"AAPL": 0.04, "MSFT": 0.04})

    link = strategy.broker_links.first()
    decision = bridge.maybe_emit_and_submit(target, link=link, autopilot=ap)

    orders = list(BrokerOrder.objects.filter(broker_account=account))
    assert len(orders) == 2
    for o in orders:
        assert o.side == "buy"  # buy weight → broker buy
        assert o.rebalance_order_id is not None  # spine FK set
        assert o.client_order_id == f"rbo-{o.rebalance_order_id}"
        assert o.status == BrokerOrder.STATUS_FILLED  # demo fills at market
        assert o.avg_fill_price == Decimal("200.0000")
    target.refresh_from_db()
    assert target.status == PortfolioTarget.AUTOPILOT_SUBMITTED  # terminal, out of ACTIVE
    assert target.status not in PortfolioTarget.ACTIVE_STATUSES
    assert decision["submitted"] == 2


# --------------------------------------------------------------------------
# Bridge: market closed → orders held pending_open (no broker call).
# --------------------------------------------------------------------------
def test_bridge_market_closed_holds_pending_open(user, monkeypatch):
    monkeypatch.setattr("apps.brokers.market_calendar.is_market_open", lambda *a, **k: False)
    account = _broker_account(user, broker="alpaca_paper")  # credentialed → honors market hours
    strategy, ap = _linked(user, account)
    target = _done_target(strategy, {"AAPL": 0.04, "MSFT": 0.04})

    bridge.maybe_emit_and_submit(target, link=strategy.broker_links.first(), autopilot=ap)

    orders = list(BrokerOrder.objects.filter(broker_account=account))
    assert len(orders) == 2
    for o in orders:
        assert o.status == BrokerOrder.STATUS_PENDING_OPEN  # held locally, not submitted
        assert o.release_after is not None  # release at next open
        assert o.queued_until_open is False  # NOT the broker-roundtripped flag
        assert o.client_order_id == f"rbo-{o.rebalance_order_id}"


# --------------------------------------------------------------------------
# Risk gate wired: an order over max_position_pct is rejected BY THE GATE.
# --------------------------------------------------------------------------
def test_risk_check_rejects_oversized_order(user):
    account = _broker_account(user, broker="alpaca_paper")
    strategy = _strategy(user, max_position_pct=Decimal("0.04"))  # 4% of 100k = $4k cap
    big = BrokerOrder.objects.create(
        broker_account=account,
        ticker="AAPL",
        side="buy",
        quantity=Decimal("100"),
        order_type="market",
        limit_price=Decimal("100"),
    )  # 100 * $100 = $10k notional >> $4k cap
    with pytest.raises(ConfirmationError) as exc:
        gate(
            big,
            GateContext(
                user=user,
                confirmation_method=BrokerOrder.CONFIRM_SCHEDULED,
                bypass_typed=True,
                risk_check=autopilot_risk.make_risk_check(strategy),
            ),
        )
    assert exc.value.code == "risk_rejected"


def test_risk_check_passes_compliant_order(user):
    account = _broker_account(user, broker="alpaca_paper")
    strategy = _strategy(user, max_position_pct=Decimal("0.04"))
    ok = BrokerOrder.objects.create(
        broker_account=account,
        ticker="AAPL",
        side="buy",
        quantity=Decimal("10"),
        order_type="market",
        limit_price=Decimal("100"),
    )  # $1k notional < $4k cap
    gate(
        ok,
        GateContext(
            user=user,
            confirmation_method=BrokerOrder.CONFIRM_SCHEDULED,
            bypass_typed=True,
            risk_check=autopilot_risk.make_risk_check(strategy),
        ),
    )
    ok.refresh_from_db()
    assert ok.status == BrokerOrder.STATUS_CONFIRMED


# --------------------------------------------------------------------------
# Risk gate prices market orders at the real mark, not a $100 placeholder.
# Regression: a 3%-cap ETF order at ~$28 was valued at $100/sh → read ~10% →
# falsely risk_rejected, so the sector/trend sleeves deployed nothing live.
# --------------------------------------------------------------------------
def test_risk_check_prices_market_order_at_real_mark(user):
    account = _broker_account(user, broker="alpaca_paper")  # $100k book
    strategy = _strategy(user, max_position_pct=Decimal("0.03"))  # 3% = $3k cap
    target = PortfolioTarget.objects.create(
        strategy=strategy,
        as_of_date=dt.date(2026, 6, 4),
        status=PortfolioTarget.DONE,
        target_weights={"UUP": 0.03},
    )
    # 106 sh × ~$28 ≈ $2,968 (within the 3% cap). At the old $100 fallback this
    # read as $10,600 = 10.6% and was rejected.
    rb = RebalanceOrder.objects.create(
        target=target,
        ticker="UUP",
        side="buy",
        quantity=Decimal("106"),
        reason="open",
        estimated_notional_usd=Decimal("2968"),
        sequence=1,
    )
    order = BrokerOrder.objects.create(
        broker_account=account,
        ticker="UUP",
        side="buy",
        quantity=Decimal("106"),
        order_type="market",
        rebalance_order=rb,
    )  # NB: no limit_price — a market order
    check = autopilot_risk.make_risk_check(strategy)
    assert check(order) == []  # priced at ~$28 → within cap

    # The gate still catches a genuinely oversized market order.
    rb_big = RebalanceOrder.objects.create(
        target=target,
        ticker="EEM",
        side="buy",
        quantity=Decimal("200"),
        reason="open",
        estimated_notional_usd=Decimal("5600"),
        sequence=2,
    )
    big = BrokerOrder.objects.create(
        broker_account=account,
        ticker="EEM",
        side="buy",
        quantity=Decimal("200"),
        order_type="market",
        rebalance_order=rb_big,
    )  # $5,600 = 5.6% > 3% cap
    assert check(big)  # non-empty reasons → rejected


def test_risk_check_market_order_falls_back_to_live_mark(user, monkeypatch):
    # No limit and no rebalance row (e.g. a manual market order) → price via the
    # live mark, not the flat placeholder.
    from apps.portfolios import valuation

    monkeypatch.setattr(
        valuation,
        "get_mark",
        lambda *a, **k: type("M", (), {"price": Decimal("28")})(),
    )
    account = _broker_account(user, broker="alpaca_paper")
    strategy = _strategy(user, max_position_pct=Decimal("0.03"))
    order = BrokerOrder.objects.create(
        broker_account=account,
        ticker="UUP",
        side="buy",
        quantity=Decimal("106"),
        order_type="market",
    )  # 106 × $28 = $2,968 < $3k cap
    assert autopilot_risk.make_risk_check(strategy)(order) == []


# --------------------------------------------------------------------------
# Venue constraint: Alpaca rejects fractional shorts → round short/cover down
# to whole shares on the credentialed path (longs + the demo book unaffected).
# --------------------------------------------------------------------------
def test_venue_quantity_rounds_fractional_shorts_whole():
    q = Decimal("17.815439")
    assert bridge._venue_quantity("short", q, is_demo=False) == Decimal("17")
    assert bridge._venue_quantity("cover", Decimal("5.7"), is_demo=False) == Decimal("5")
    assert bridge._venue_quantity("short", Decimal("0.42"), is_demo=False) is None  # <1 sh → drop
    assert bridge._venue_quantity("buy", q, is_demo=False) == q      # long unchanged
    assert bridge._venue_quantity("sell", q, is_demo=False) == q     # long unchanged
    assert bridge._venue_quantity("short", q, is_demo=True) == q     # demo book unaffected


# --------------------------------------------------------------------------
# needs_reauth: a transient 401/403 must NOT darken a healthy account — confirm
# with a fresh probe before flagging (only a second auth failure is "dead").
# --------------------------------------------------------------------------
def test_credentials_confirmed_dead_distinguishes_blip_from_dead(user, monkeypatch):
    from apps.brokers import reconcile

    account = _broker_account(user, broker="alpaca_paper")

    class _Dead:
        def get_account(self):
            raise BrokerAuthError("401")

    class _Live:
        def get_account(self):
            return object()

    monkeypatch.setattr(reconcile, "get_broker", lambda a: _Dead())
    assert reconcile.credentials_confirmed_dead(account) is True
    monkeypatch.setattr(reconcile, "get_broker", lambda a: _Live())
    assert reconcile.credentials_confirmed_dead(account) is False


def test_credentials_confirmed_dead_demo_preserves_behavior(user):
    from apps.brokers import reconcile

    account = _broker_account(user, broker="mock")  # AUTH_NONE → no remote session
    assert reconcile.credentials_confirmed_dead(account) is True


def test_transient_submit_auth_keeps_account_active(user, monkeypatch):
    # A 401/403 during submission whose follow-up probe SUCCEEDS is a blip — the
    # account must stay ACTIVE so the other orders + next tick recover.
    from apps.brokers import reconcile

    monkeypatch.setattr("apps.brokers.market_calendar.is_market_open", lambda *a, **k: True)
    account = _broker_account(user, broker="alpaca_paper")
    strategy, ap = _linked(user, account)

    class _FlakyBroker:
        def submit_order(self, ticket):
            raise BrokerAuthError("transient 401")

        def get_account(self):           # confirmation probe succeeds → blip
            return object()

    monkeypatch.setattr(reconcile, "get_broker", lambda a: _FlakyBroker())
    bridge.maybe_emit_and_submit(
        _done_target(strategy, {"AAPL": 0.04}),
        link=strategy.broker_links.first(), autopilot=ap,
    )
    account.refresh_from_db()
    assert account.connection_status == BrokerAccount.STATUS_ACTIVE


def test_confirmed_dead_submit_auth_darkens_account(user, monkeypatch):
    # When the follow-up probe ALSO auth-fails the credentials are genuinely
    # dead → the account is flipped to needs_reauth (preserved behavior).
    from apps.brokers import reconcile

    monkeypatch.setattr("apps.brokers.market_calendar.is_market_open", lambda *a, **k: True)
    account = _broker_account(user, broker="alpaca_paper")
    strategy, ap = _linked(user, account)

    class _DeadBroker:
        def submit_order(self, ticket):
            raise BrokerAuthError("401")

        def get_account(self):           # probe ALSO auth-fails → dead
            raise BrokerAuthError("401")

    monkeypatch.setattr(reconcile, "get_broker", lambda a: _DeadBroker())
    bridge.maybe_emit_and_submit(
        _done_target(strategy, {"AAPL": 0.04}),
        link=strategy.broker_links.first(), autopilot=ap,
    )
    account.refresh_from_db()
    assert account.connection_status == BrokerAccount.STATUS_NEEDS_REAUTH


# --------------------------------------------------------------------------
# Live hard-block: link rejects live; gate still blocks scheduled × live.
# --------------------------------------------------------------------------
def test_link_rejects_live_account(user):
    live = _broker_account(user, broker="alpaca_paper", mode=BrokerAccount.MODE_LIVE)
    strategy = _strategy(user)
    with pytest.raises(ValidationError):
        StrategyBrokerLink(strategy=strategy, broker_account=live).save()


def test_gate_still_blocks_scheduled_live(user):
    live = _broker_account(user, broker="alpaca_paper", mode=BrokerAccount.MODE_LIVE)
    order = BrokerOrder.objects.create(
        broker_account=live,
        ticker="AAPL",
        side="buy",
        quantity=Decimal("1"),
        order_type="market",
        limit_price=Decimal("100"),
    )
    with pytest.raises(ConfirmationError) as exc:
        gate(order, GateContext(user=user, confirmation_method=BrokerOrder.CONFIRM_SCHEDULED))
    assert exc.value.code == "scheduled_live_blocked"


# --------------------------------------------------------------------------
# Backward-compat: no link → bridge is a no-op (cycle stays 'done').
# --------------------------------------------------------------------------
def test_no_link_is_noop(user):
    strategy = _strategy(user)  # no link, no autopilot
    target = _done_target(strategy, {"AAPL": 0.04})
    result = bridge._finalize_target(target)
    target.refresh_from_db()
    assert result is None
    assert target.status == PortfolioTarget.DONE
    assert BrokerOrder.objects.count() == 0


def test_disabled_autopilot_is_noop(user):
    account = _broker_account(user, broker="mock")
    strategy, ap = _linked(user, account, enabled=False)
    target = _done_target(strategy, {"AAPL": 0.04})
    assert bridge._finalize_target(target) is None
    target.refresh_from_db()
    assert target.status == PortfolioTarget.DONE
    assert BrokerOrder.objects.count() == 0


def test_halted_autopilot_is_noop(user):
    account = _broker_account(user, broker="mock")
    strategy, ap = _linked(user, account, state=StrategyAutopilot.STATE_HALTED)
    target = _done_target(strategy, {"AAPL": 0.04})
    assert bridge._finalize_target(target) is None
    assert BrokerOrder.objects.count() == 0


# --------------------------------------------------------------------------
# Idempotency: AutopilotRun unique (autopilot, fire_time) blocks a double fire.
# --------------------------------------------------------------------------
def test_autopilot_run_fire_time_unique(user):
    account = _broker_account(user, broker="mock")
    strategy, ap = _linked(user, account)
    ft = timezone.now()
    AutopilotRun.objects.create(autopilot=ap, fire_time_utc=ft)
    with pytest.raises(IntegrityError):
        AutopilotRun.objects.create(autopilot=ap, fire_time_utc=ft)


# --------------------------------------------------------------------------
# Dispatcher: due + enabled fires once and advances; halted/disabled skipped;
# market-gate skips non-trading days.
# --------------------------------------------------------------------------
def test_dispatch_fires_due_autopilot(user, monkeypatch):
    calls = []
    monkeypatch.setattr(tasks_autopilot.run_autopilot_cycle, "delay", lambda rid: calls.append(rid))
    account = _broker_account(user, broker="mock")
    strategy, ap = _linked(user, account)
    ap.is_market_aware = False
    ap.next_run_at = timezone.now() - dt.timedelta(minutes=1)
    ap.save(update_fields=["is_market_aware", "next_run_at"])

    res = tasks_autopilot.dispatch_due_autopilots()

    assert res["dispatched"] == 1
    assert len(calls) == 1
    ap.refresh_from_db()
    assert ap.next_run_at > timezone.now()  # advanced
    assert AutopilotRun.objects.filter(autopilot=ap).count() == 1


def test_dispatch_skips_halted_and_disabled(user, monkeypatch):
    monkeypatch.setattr(tasks_autopilot.run_autopilot_cycle, "delay", lambda rid: None)
    account = _broker_account(user, broker="mock")
    _, halted = _linked(user, account, state=StrategyAutopilot.STATE_HALTED)
    halted.is_market_aware = False
    halted.next_run_at = timezone.now() - dt.timedelta(minutes=1)
    halted.save(update_fields=["is_market_aware", "next_run_at"])

    res = tasks_autopilot.dispatch_due_autopilots()
    assert res["dispatched"] == 0
    assert AutopilotRun.objects.count() == 0


def test_dispatch_market_gate_skips_non_trading_day(user, monkeypatch):
    monkeypatch.setattr(tasks_autopilot.run_autopilot_cycle, "delay", lambda rid: None)
    # Force a non-trading day deterministically (weekend/holiday).
    monkeypatch.setattr("apps.brokers.market_calendar.is_trading_day", lambda d: False)
    account = _broker_account(user, broker="mock")
    strategy, ap = _linked(user, account)
    ap.is_market_aware = True
    ap.next_run_at = timezone.now() - dt.timedelta(minutes=1)  # due now, but not a trading day
    ap.save(update_fields=["is_market_aware", "next_run_at"])

    res = tasks_autopilot.dispatch_due_autopilots()
    assert res["skipped_market"] == 1
    assert AutopilotRun.objects.count() == 0
