"""Bracket / OTO / OCO + standalone stop / trailing tests (P3a, ADR 0015).

Stub-client style (extends the P3a-4 harness): `_make_client` is monkey-patched
to a `StubTradingClient` that records the SDK request objects and returns
canned Order shapes (incl. nested `legs[]`). The HTTP boundary never runs;
the live Alpaca round-trip is the manual bracket checklist.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth import get_user_model

from apps.brokers.adapters import alpaca_paper as alpaca_mod
from apps.brokers.adapters.alpaca_paper import AlpacaPaperBroker
from apps.brokers.brackets import (
    GROUP_CANCELLED,
    GROUP_CLOSED,
    GROUP_CONFIRMED,
    GROUP_DRAFT,
    GROUP_ERROR,
    GROUP_WORKING,
    create_group,
    derive_group_status,
)
from apps.brokers.confirmation import GateContext, gate_bracket
from apps.brokers.credentials import set_api_key_secret
from apps.brokers.idempotency import submit_bracket_idempotent
from apps.brokers.interfaces import BrokerError, OrderTicket
from apps.brokers.models import BrokerAccount, BrokerOrder
from apps.brokers.reconcile import poll_open_orders_for_account
from apps.portfolios.models import LedgerEntry, Portfolio

User = get_user_model()


# ---------------------------------------------------------------------------
# SDK stubs (extends test_alpaca_paper_adapter's shapes with grouped fields)
# ---------------------------------------------------------------------------


@dataclass
class StubOrder:
    id: str = "ord-abc"
    client_order_id: str = "coid-1"
    symbol: str = "AAPL"
    side: str = "buy"
    qty: str = "10"
    order_type: str = "market"
    limit_price: str | None = None
    stop_price: str | None = None
    trail_price: str | None = None
    trail_percent: str | None = None
    order_class: str = "simple"
    time_in_force: str = "day"
    status: str = "new"
    filled_qty: str = "0"
    filled_avg_price: str | None = None
    filled_at: datetime | None = None
    updated_at: datetime | None = None
    legs: list[StubOrder] = field(default_factory=list)

    def model_dump(self) -> dict:
        return {
            "id": self.id, "client_order_id": self.client_order_id,
            "symbol": self.symbol, "side": self.side, "qty": self.qty,
            "order_type": self.order_type, "limit_price": self.limit_price,
            "stop_price": self.stop_price, "order_class": self.order_class,
            "status": self.status, "filled_qty": self.filled_qty,
            "filled_avg_price": self.filled_avg_price,
        }


@dataclass
class StubAccount:
    account_number: str = "PA-TEST-123"
    cash: str = "50000.00"
    buying_power: str = "100000.00"
    equity: str = "75000.00"
    currency: str = "USD"

    def model_dump(self) -> dict:
        return {"account_number": self.account_number, "cash": self.cash}


@dataclass
class StubTradingClient:
    account: StubAccount = field(default_factory=StubAccount)
    positions: list = field(default_factory=list)
    orders: list[StubOrder] = field(default_factory=list)
    submit_response: Any = None
    submit_exception: Exception | None = None
    by_client_id: dict[str, StubOrder] = field(default_factory=dict)
    by_id: dict[str, StubOrder] = field(default_factory=dict)
    calls: list[tuple[str, dict]] = field(default_factory=list)

    def get_account(self):
        self.calls.append(("get_account", {}))
        return self.account

    def get_all_positions(self):
        self.calls.append(("get_all_positions", {}))
        return self.positions

    def get_orders(self, filter=None):  # noqa: A002 - matches SDK signature
        self.calls.append(("get_orders", {"filter": filter}))
        return self.orders

    def submit_order(self, order_data=None):
        self.calls.append(("submit_order", {"order_data": order_data}))
        if self.submit_exception is not None:
            raise self.submit_exception
        if self.submit_response is not None:
            return self.submit_response
        return StubOrder(client_order_id=getattr(order_data, "client_order_id", ""))

    def get_order_by_id(self, order_id):
        self.calls.append(("get_order_by_id", {"order_id": order_id}))
        return self.by_id.get(order_id) or StubOrder(id=order_id)

    def get_order_by_client_id(self, client_id):
        self.calls.append(("get_order_by_client_id", {"client_order_id": client_id}))
        return self.by_client_id.get(client_id)

    def cancel_order_by_id(self, order_id):
        self.calls.append(("cancel_order_by_id", {"order_id": order_id}))
        return None


def _make_apierror(status: int, message: str, code: int = 42210000):
    import json as _json

    from alpaca.common.exceptions import APIError

    class _R:
        status_code = status

    class _H:
        response = _R()
        request = None

    return APIError(_json.dumps({"code": code, "message": message}), http_error=_H())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def user(db):
    return User.objects.create_user(email="brackets@example.com", password="x" * 12)


@pytest.fixture
def paper_account(user) -> BrokerAccount:
    p = Portfolio.objects.create(
        user=user, name="Broker · Alpaca paper",
        kind=Portfolio.KIND_BROKER, cash_balance=Decimal("100000"),
    )
    acc = BrokerAccount.objects.create(
        user=user, broker="alpaca_paper", mode="paper",
        account_id="PA-TEST-123", label="alpaca test", portfolio=p,
        connection_status=BrokerAccount.STATUS_ACTIVE,
    )
    set_api_key_secret(acc, api_key="PK-TEST", api_secret="SK-TEST")
    return acc


@pytest.fixture
def stub_client() -> StubTradingClient:
    return StubTradingClient()


@pytest.fixture
def broker(paper_account, stub_client, monkeypatch) -> AlpacaPaperBroker:
    monkeypatch.setattr(
        alpaca_mod, "_make_client", lambda api_key, api_secret: stub_client,
    )
    return AlpacaPaperBroker(paper_account)


def _bracket_rows(account, entry_status="draft", sl_status="draft",
                  tp_status="draft", *, side="buy") -> BrokerOrder:
    """Build a bracket group directly with chosen per-leg statuses."""
    anchor = create_group(
        account=account, decision=None, ticker="AAPL", side=side,
        quantity=Decimal("10"), time_in_force="day", order_class="bracket",
        entry_order_type="limit", entry_limit_price=Decimal("100"),
        take_profit_limit_price=Decimal("110"),
        stop_loss_stop_price=Decimal("95"),
    )
    BrokerOrder.objects.filter(pk=anchor.pk).update(status=entry_status)
    BrokerOrder.objects.filter(
        parent_order=anchor, leg_role=BrokerOrder.LEG_STOP_LOSS,
    ).update(status=sl_status)
    BrokerOrder.objects.filter(
        parent_order=anchor, leg_role=BrokerOrder.LEG_TAKE_PROFIT,
    ).update(status=tp_status)
    anchor.refresh_from_db()
    return anchor


# ---------------------------------------------------------------------------
# create_group — row shapes
# ---------------------------------------------------------------------------


def test_create_bracket_builds_three_rows(paper_account):
    anchor = _bracket_rows(paper_account)
    assert anchor.leg_role == BrokerOrder.LEG_ENTRY
    assert anchor.parent_order_id is None
    legs = list(anchor.child_legs.all())
    assert len(legs) == 2
    sl = next(leg for leg in legs if leg.leg_role == BrokerOrder.LEG_STOP_LOSS)
    tp = next(leg for leg in legs if leg.leg_role == BrokerOrder.LEG_TAKE_PROFIT)
    # Exits are the opposite side of a buy entry.
    assert sl.side == "sell" and tp.side == "sell"
    assert sl.order_type == "stop" and sl.stop_price == Decimal("95")
    assert tp.order_type == "limit" and tp.limit_price == Decimal("110")
    # One shared group_id across all three.
    assert anchor.group_id == sl.group_id == tp.group_id


def test_create_oco_anchor_is_take_profit(paper_account):
    anchor = create_group(
        account=paper_account, decision=None, ticker="AAPL", side="sell",
        quantity=Decimal("10"), time_in_force="gtc", order_class="oco",
        take_profit_limit_price=Decimal("120"),
        stop_loss_stop_price=Decimal("90"),
    )
    assert anchor.leg_role == BrokerOrder.LEG_TAKE_PROFIT
    assert anchor.parent_order_id is None
    assert anchor.limit_price == Decimal("120")
    legs = list(anchor.child_legs.all())
    assert len(legs) == 1
    assert legs[0].leg_role == BrokerOrder.LEG_STOP_LOSS
    assert legs[0].side == "sell" and anchor.side == "sell"


def test_create_oto_single_exit(paper_account):
    anchor = create_group(
        account=paper_account, decision=None, ticker="AAPL", side="buy",
        quantity=Decimal("10"), time_in_force="day", order_class="oto",
        entry_order_type="market",
        stop_loss_stop_price=Decimal("95"),
    )
    legs = list(anchor.child_legs.all())
    assert len(legs) == 1
    assert legs[0].leg_role == BrokerOrder.LEG_STOP_LOSS


# ---------------------------------------------------------------------------
# derive_group_status — the rule table
# ---------------------------------------------------------------------------


def test_group_status_draft_then_confirmed(paper_account):
    a = _bracket_rows(paper_account, "draft", "draft", "draft")
    assert derive_group_status(a) == GROUP_DRAFT
    a = _bracket_rows(paper_account, "confirmed", "confirmed", "confirmed")
    assert derive_group_status(a) == GROUP_CONFIRMED


def test_group_status_working(paper_account):
    a = _bracket_rows(paper_account, "submitted", "submitted", "submitted")
    assert derive_group_status(a) == GROUP_WORKING
    # entry filled + exits working (held → live) is still working.
    a = _bracket_rows(paper_account, "filled", "submitted", "submitted")
    assert derive_group_status(a) == GROUP_WORKING


def test_group_status_closed_on_exit_fill(paper_account):
    # stop-loss fills, take-profit cancelled by Alpaca's native OCO → closed.
    a = _bracket_rows(paper_account, "filled", "filled", "cancelled")
    assert derive_group_status(a) == GROUP_CLOSED


def test_group_status_closed_on_user_teardown(paper_account):
    # entry filled, user cancels both exits — position open, not cancelled/error.
    a = _bracket_rows(paper_account, "filled", "cancelled", "cancelled")
    assert derive_group_status(a) == GROUP_CLOSED


def test_group_status_cancelled_before_fill(paper_account):
    a = _bracket_rows(paper_account, "cancelled", "cancelled", "cancelled")
    assert derive_group_status(a) == GROUP_CANCELLED
    # entry rejected pre-fill → nothing opened → cancelled.
    a = _bracket_rows(paper_account, "rejected", "cancelled", "cancelled")
    assert derive_group_status(a) == GROUP_CANCELLED


def test_group_status_error_on_leg_error(paper_account):
    a = _bracket_rows(paper_account, "submitted", "error", "submitted")
    assert derive_group_status(a) == GROUP_ERROR


def test_group_status_error_on_rejected_protective_while_live(paper_account):
    # entry filled (position live), stop-loss rejected → safety event.
    a = _bracket_rows(paper_account, "filled", "rejected", "submitted")
    assert derive_group_status(a) == GROUP_ERROR


# ---------------------------------------------------------------------------
# Adapter request-building — standalone stop / stop_limit / trailing
# ---------------------------------------------------------------------------


def _last_submit(stub):
    return [c for c in stub.calls if c[0] == "submit_order"][-1][1]["order_data"]


def test_submit_stop_builds_stop_request(broker, stub_client):
    from alpaca.trading.requests import StopOrderRequest
    ticket = OrderTicket(
        client_order_id="c1", ticker="AAPL", side="sell", quantity=Decimal("3"),
        order_type="stop", stop_price=Decimal("95"),
    )
    broker.submit_order(ticket)
    req = _last_submit(stub_client)
    assert isinstance(req, StopOrderRequest)
    assert float(req.stop_price) == 95.0


def test_submit_stop_limit_builds_request(broker, stub_client):
    from alpaca.trading.requests import StopLimitOrderRequest
    ticket = OrderTicket(
        client_order_id="c2", ticker="AAPL", side="sell", quantity=Decimal("3"),
        order_type="stop_limit", stop_price=Decimal("95"), limit_price=Decimal("94"),
    )
    broker.submit_order(ticket)
    req = _last_submit(stub_client)
    assert isinstance(req, StopLimitOrderRequest)
    assert float(req.stop_price) == 95.0 and float(req.limit_price) == 94.0


def test_submit_trailing_by_price_and_percent(broker, stub_client):
    from alpaca.trading.requests import TrailingStopOrderRequest
    broker.submit_order(OrderTicket(
        client_order_id="c3", ticker="AAPL", side="sell", quantity=Decimal("3"),
        order_type="trailing_stop", trail_price=Decimal("2.5"),
    ))
    req = _last_submit(stub_client)
    assert isinstance(req, TrailingStopOrderRequest)
    assert float(req.trail_price) == 2.5
    broker.submit_order(OrderTicket(
        client_order_id="c4", ticker="AAPL", side="sell", quantity=Decimal("3"),
        order_type="trailing_stop", trail_percent=Decimal("5"),
    ))
    req = _last_submit(stub_client)
    assert float(req.trail_percent) == 5.0


def test_latent_bug_stop_without_price_rejected(broker):
    """A stop with no trigger is rejected — never silently market-filled."""
    with pytest.raises(BrokerError, match="stop_price"):
        broker.submit_order(OrderTicket(
            client_order_id="c5", ticker="AAPL", side="sell",
            quantity=Decimal("3"), order_type="stop",
        ))


def test_trailing_requires_exactly_one_offset(broker):
    with pytest.raises(BrokerError, match="exactly one"):
        broker.submit_order(OrderTicket(
            client_order_id="c6", ticker="AAPL", side="sell",
            quantity=Decimal("3"), order_type="trailing_stop",
            trail_price=Decimal("2"), trail_percent=Decimal("5"),
        ))


# ---------------------------------------------------------------------------
# Adapter request-building — bracket / OTO / OCO
# ---------------------------------------------------------------------------


def test_submit_bracket_builds_grouped_request(broker, stub_client):
    from alpaca.trading.enums import OrderClass
    ticket = OrderTicket(
        client_order_id="b1", ticker="AAPL", side="buy", quantity=Decimal("10"),
        order_type="limit", limit_price=Decimal("100"), order_class="bracket",
        take_profit_limit_price=Decimal("110"), stop_loss_stop_price=Decimal("95"),
    )
    broker.submit_bracket(ticket)
    req = _last_submit(stub_client)
    assert req.order_class == OrderClass.BRACKET
    assert float(req.take_profit.limit_price) == 110.0
    assert float(req.stop_loss.stop_price) == 95.0
    assert req.stop_loss.limit_price is None  # stop-market exit


def test_submit_bracket_stop_limit_exit(broker, stub_client):
    ticket = OrderTicket(
        client_order_id="b2", ticker="AAPL", side="buy", quantity=Decimal("10"),
        order_type="market", order_class="bracket",
        take_profit_limit_price=Decimal("110"), stop_loss_stop_price=Decimal("95"),
        stop_loss_limit_price=Decimal("94"),
    )
    broker.submit_bracket(ticket)
    req = _last_submit(stub_client)
    assert float(req.stop_loss.limit_price) == 94.0


def test_submit_oto_exactly_one_exit(broker, stub_client):
    from alpaca.trading.enums import OrderClass
    ticket = OrderTicket(
        client_order_id="o1", ticker="AAPL", side="buy", quantity=Decimal("10"),
        order_type="market", order_class="oto", stop_loss_stop_price=Decimal("95"),
    )
    broker.submit_bracket(ticket)
    req = _last_submit(stub_client)
    assert req.order_class == OrderClass.OTO
    assert req.take_profit is None
    assert float(req.stop_loss.stop_price) == 95.0


def test_submit_oto_rejects_two_exits(broker):
    with pytest.raises(BrokerError, match="exactly one"):
        broker.submit_bracket(OrderTicket(
            client_order_id="o2", ticker="AAPL", side="buy", quantity=Decimal("10"),
            order_type="market", order_class="oto",
            take_profit_limit_price=Decimal("110"), stop_loss_stop_price=Decimal("95"),
        ))


def test_submit_protective_builds_oco(broker, stub_client):
    from alpaca.trading.enums import OrderClass
    ticket = OrderTicket(
        client_order_id="oco1", ticker="AAPL", side="sell", quantity=Decimal("10"),
        order_type="limit", order_class="oco",
        take_profit_limit_price=Decimal("120"), stop_loss_stop_price=Decimal("90"),
    )
    broker.submit_protective(ticket)
    req = _last_submit(stub_client)
    assert req.order_class == OrderClass.OCO
    assert float(req.limit_price) == 120.0          # take-profit on the base
    # Alpaca requires the nested take_profit leg too (422 without it).
    assert float(req.take_profit.limit_price) == 120.0
    assert float(req.stop_loss.stop_price) == 90.0


# ---------------------------------------------------------------------------
# Adapter response-mapping — parent + legs[]
# ---------------------------------------------------------------------------


def test_snapshot_maps_parent_and_legs(broker, stub_client):
    parent = StubOrder(
        id="entry-1", client_order_id="b1", symbol="AAPL", side="buy", qty="10",
        order_type="limit", order_class="bracket", status="new",
        legs=[
            StubOrder(id="tp-1", symbol="AAPL", side="sell", order_type="limit",
                      limit_price="110", status="held"),
            StubOrder(id="sl-1", symbol="AAPL", side="sell", order_type="stop",
                      stop_price="95", status="held"),
        ],
    )
    stub_client.by_id["entry-1"] = parent
    snap = broker.get_order("entry-1")
    assert snap.order_class == "bracket" and snap.leg_role == "entry"
    assert len(snap.legs) == 2
    tp = next(leg for leg in snap.legs if leg.order_type == "limit")
    sl = next(leg for leg in snap.legs if leg.order_type == "stop")
    assert tp.leg_role == "take_profit" and tp.broker_order_id == "tp-1"
    assert sl.leg_role == "stop_loss" and sl.stop_price == Decimal("95")


def test_snapshot_maps_full_type_set_no_collapse(broker, stub_client):
    # The old code collapsed stop_limit→limit and stop→market. It must not now.
    for raw_type, expect in [("stop", "stop"), ("stop_limit", "stop_limit"),
                             ("trailing_stop", "trailing_stop")]:
        stub_client.by_id[f"x-{raw_type}"] = StubOrder(id=f"x-{raw_type}",
                                                       order_type=raw_type)
        snap = broker.get_order(f"x-{raw_type}")
        assert snap.order_type == expect


# ---------------------------------------------------------------------------
# submit_bracket_idempotent — leg backfill + idempotency
# ---------------------------------------------------------------------------


def _accepted_bracket_parent(coid):
    return StubOrder(
        id="entry-1", client_order_id=coid, symbol="AAPL", side="buy", qty="10",
        order_type="limit", order_class="bracket", status="accepted",
        legs=[
            StubOrder(id="tp-1", symbol="AAPL", side="sell", order_type="limit",
                      limit_price="110", status="held"),
            StubOrder(id="sl-1", symbol="AAPL", side="sell", order_type="stop",
                      stop_price="95", status="held"),
        ],
    )


def test_submit_bracket_idempotent_backfills_legs(broker, stub_client, paper_account):
    anchor = _bracket_rows(paper_account, "confirmed", "confirmed", "confirmed")
    stub_client.submit_response = _accepted_bracket_parent(str(anchor.client_order_id))
    submit_bracket_idempotent(anchor=anchor, broker=broker)
    anchor.refresh_from_db()
    assert anchor.idempotency_state == BrokerOrder.IDEM_ACKNOWLEDGED
    assert anchor.broker_order_id == "entry-1"
    sl = anchor.child_legs.get(leg_role=BrokerOrder.LEG_STOP_LOSS)
    tp = anchor.child_legs.get(leg_role=BrokerOrder.LEG_TAKE_PROFIT)
    assert tp.broker_order_id == "tp-1" and sl.broker_order_id == "sl-1"
    assert tp.idempotency_state == BrokerOrder.IDEM_ACKNOWLEDGED


def test_retried_bracket_resolves_existing_with_legs(broker, stub_client, paper_account):
    """A 422 on the entry's client_order_id resolves the existing parent WITH
    legs rather than creating a second bracket."""
    anchor = _bracket_rows(paper_account, "confirmed", "confirmed", "confirmed")
    coid = str(anchor.client_order_id)
    stub_client.submit_exception = _make_apierror(422, "client_order_id must be unique")
    stub_client.by_client_id[coid] = _accepted_bracket_parent(coid)
    submit_bracket_idempotent(anchor=anchor, broker=broker)
    anchor.refresh_from_db()
    assert anchor.broker_order_id == "entry-1"
    assert anchor.child_legs.get(leg_role=BrokerOrder.LEG_STOP_LOSS).broker_order_id == "sl-1"


# ---------------------------------------------------------------------------
# Lifecycle — entry fills → SL fills → TP cancelled → closed (+ ledger)
# ---------------------------------------------------------------------------


def test_bracket_lifecycle_and_ledger_invariant(broker, stub_client, paper_account):
    initial_cash = paper_account.portfolio.cash_balance
    anchor = _bracket_rows(paper_account, "confirmed", "confirmed", "confirmed")
    coid = str(anchor.client_order_id)
    stub_client.submit_response = _accepted_bracket_parent(coid)
    submit_bracket_idempotent(anchor=anchor, broker=broker)
    anchor.refresh_from_db()

    now = datetime.now(tz=UTC)
    # Entry filled @100; stop-loss filled @95; take-profit cancelled (OCO).
    stub_client.by_id["entry-1"] = StubOrder(
        id="entry-1", client_order_id=coid, symbol="AAPL", side="buy", qty="10",
        order_type="limit", order_class="bracket", status="filled",
        filled_qty="10", filled_avg_price="100",
        legs=[
            StubOrder(id="tp-1", symbol="AAPL", side="sell", order_type="limit",
                      limit_price="110", status="canceled", filled_qty="0"),
            StubOrder(id="sl-1", symbol="AAPL", side="sell", order_type="stop",
                      stop_price="95", status="filled", filled_qty="10",
                      filled_avg_price="95"),
        ],
    )
    # Fills happen after the order was created (the poll's `since` cutoff).
    stub_client.orders = [
        StubOrder(id="entry-1", symbol="AAPL", side="buy", filled_qty="10",
                  filled_avg_price="100", status="filled",
                  filled_at=now + timedelta(minutes=1)),
        StubOrder(id="sl-1", symbol="AAPL", side="sell", filled_qty="10",
                  filled_avg_price="95", status="filled",
                  filled_at=now + timedelta(minutes=2)),
    ]
    poll_open_orders_for_account(paper_account)

    anchor.refresh_from_db()
    sl = anchor.child_legs.get(leg_role=BrokerOrder.LEG_STOP_LOSS)
    tp = anchor.child_legs.get(leg_role=BrokerOrder.LEG_TAKE_PROFIT)
    assert anchor.status == BrokerOrder.STATUS_FILLED
    assert sl.status == BrokerOrder.STATUS_FILLED
    assert tp.status == BrokerOrder.STATUS_CANCELLED
    assert derive_group_status(anchor) == GROUP_CLOSED

    # One broker_fill ledger entry per leg fill (entry buy + stop-loss sell).
    portfolio = paper_account.portfolio
    fills = LedgerEntry.objects.filter(
        portfolio=portfolio, kind=LedgerEntry.KIND_BROKER_FILL,
    )
    assert fills.count() == 2

    # Ledger invariant: initial cash + Σ cash_delta == cash_balance.
    portfolio.refresh_from_db()
    total_delta = sum(
        (e.cash_delta for e in LedgerEntry.objects.filter(portfolio=portfolio)),
        Decimal("0"),
    )
    assert initial_cash + total_delta == portfolio.cash_balance
    # Round-trip flat: bought 10 @100, sold 10 @95 → -50 net cash, no position.
    assert portfolio.cash_balance == initial_cash - Decimal("50.00")
    assert not portfolio.positions.filter(ticker="AAPL").exists()


# ---------------------------------------------------------------------------
# gate_bracket — one confirmation, max-loss / target-gain
# ---------------------------------------------------------------------------


def test_gate_bracket_one_confirmation_and_max_loss(paper_account, user):
    anchor = _bracket_rows(paper_account, "draft", "draft", "draft")
    ctx = GateContext(user=user, quote_price=Decimal("100"))
    result = gate_bracket(anchor, ctx)
    anchor.refresh_from_db()
    # Confirmation recorded on the entry; legs inherit it.
    assert anchor.status == BrokerOrder.STATUS_CONFIRMED
    assert anchor.confirmed_by_id == user.id
    for leg in anchor.child_legs.all():
        assert leg.status == BrokerOrder.STATUS_CONFIRMED
        assert leg.confirmed_by_id == user.id
    # Long 10 @100: stop 95 → max loss 50; tp 110 → gain 100.
    assert result.max_loss == Decimal("50.00")
    assert result.target_gain == Decimal("100.00")


def test_gate_bracket_short_signs(paper_account, user):
    anchor = _bracket_rows(paper_account, "draft", "draft", "draft", side="sell")
    # Short entry @100: stop above (loss), take-profit below (gain).
    BrokerOrder.objects.filter(parent_order=anchor,
                               leg_role=BrokerOrder.LEG_STOP_LOSS).update(
        stop_price=Decimal("105"))
    BrokerOrder.objects.filter(parent_order=anchor,
                               leg_role=BrokerOrder.LEG_TAKE_PROFIT).update(
        limit_price=Decimal("90"))
    ctx = GateContext(user=user, quote_price=Decimal("100"))
    result = gate_bracket(anchor, ctx)
    assert result.max_loss == Decimal("50.00")   # (105-100)*10
    assert result.target_gain == Decimal("100.00")  # (100-90)*10


def test_gate_bracket_entry_notional_drives_typed_gate(paper_account, user):
    from apps.brokers.confirmation import ConfirmationError
    # 50 shares @100 = $5,000 entry notional → typed-ticker gate fires.
    anchor = create_group(
        account=paper_account, decision=None, ticker="AAPL", side="buy",
        quantity=Decimal("50"), time_in_force="day", order_class="bracket",
        entry_order_type="limit", entry_limit_price=Decimal("100"),
        take_profit_limit_price=Decimal("110"), stop_loss_stop_price=Decimal("95"),
    )
    ctx = GateContext(user=user, quote_price=Decimal("100"), typed_confirmation="")
    with pytest.raises(ConfirmationError, match="typing the"):
        gate_bracket(anchor, ctx)
    # Typing the ticker satisfies it.
    ctx2 = GateContext(user=user, quote_price=Decimal("100"), typed_confirmation="AAPL")
    gate_bracket(anchor, ctx2)
    anchor.refresh_from_db()
    assert anchor.status == BrokerOrder.STATUS_CONFIRMED


# ---------------------------------------------------------------------------
# View layer — capability gate, whole-share force, child-confirm rejection
# ---------------------------------------------------------------------------


@pytest.fixture
def demo_account(user) -> BrokerAccount:
    p = Portfolio.objects.create(
        user=user, name="Broker · Demo", kind=Portfolio.KIND_BROKER,
        cash_balance=Decimal("100000"),
    )
    return BrokerAccount.objects.create(
        user=user, broker="mock", mode="paper", account_id="DEMO-1",
        label="demo", portfolio=p, connection_status=BrokerAccount.STATUS_ACTIVE,
    )


@pytest.fixture
def api(user):
    from rest_framework.test import APIClient
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def test_create_rejects_unsupported_order_type(api, demo_account):
    # The demo broker supports market/limit/stop — not trailing_stop.
    r = api.post("/api/broker/orders/", {
        "broker_account": demo_account.id, "ticker": "AAPL", "side": "sell",
        "quantity": "5", "order_type": "trailing_stop", "trail_percent": "5",
    }, format="json")
    assert r.status_code == 400
    assert "trailing_stop" in str(r.data)


def test_create_rejects_bracket_on_non_supporting_broker(api, demo_account):
    r = api.post("/api/broker/orders/", {
        "broker_account": demo_account.id, "ticker": "AAPL", "side": "buy",
        "quantity": "5", "order_type": "market", "order_class": "bracket",
        "take_profit_limit_price": "110", "stop_loss_stop_price": "95",
    }, format="json")
    assert r.status_code == 400
    assert "bracket" in str(r.data).lower()


def test_create_bracket_via_api_returns_anchor_with_legs(api, paper_account):
    r = api.post("/api/broker/orders/", {
        "broker_account": paper_account.id, "ticker": "AAPL", "side": "buy",
        "quantity": "10", "order_type": "limit", "limit_price": "100",
        "order_class": "bracket",
        "take_profit_limit_price": "110", "stop_loss_stop_price": "95",
    }, format="json")
    assert r.status_code == 201
    assert r.data["leg_role"] == "entry"
    assert r.data["group_status"] == "draft"
    assert len(r.data["legs"]) == 2
    roles = {leg["leg_role"] for leg in r.data["legs"]}
    assert roles == {"stop_loss", "take_profit"}


def test_create_bracket_forces_whole_share(api, paper_account):
    # Fractional requested, but a bracket forces whole shares (10.7 → 10).
    r = api.post("/api/broker/orders/", {
        "broker_account": paper_account.id, "ticker": "AAPL", "side": "buy",
        "quantity": "10.7", "quantity_mode": "fractional",
        "order_type": "market", "order_class": "oto",
        "stop_loss_stop_price": "95",
    }, format="json")
    assert r.status_code == 201
    assert Decimal(r.data["quantity"]) == Decimal("10")


def test_create_sub_share_bracket_rejected(api, paper_account):
    r = api.post("/api/broker/orders/", {
        "broker_account": paper_account.id, "ticker": "AAPL", "side": "buy",
        "quantity": "0.5", "order_type": "market", "order_class": "oto",
        "stop_loss_stop_price": "95",
    }, format="json")
    assert r.status_code == 400
    assert "zero" in str(r.data).lower()


def test_standalone_stop_limit_via_api(api, paper_account):
    r = api.post("/api/broker/orders/", {
        "broker_account": paper_account.id, "ticker": "AAPL", "side": "sell",
        "quantity": "5", "order_type": "stop_limit",
        "stop_price": "95", "limit_price": "94",
    }, format="json")
    assert r.status_code == 201
    assert r.data["order_type"] == "stop_limit"
    assert r.data["stop_price"] == "95.0000" and r.data["limit_price"] == "94.0000"


def test_stop_without_price_rejected_at_view(api, paper_account):
    r = api.post("/api/broker/orders/", {
        "broker_account": paper_account.id, "ticker": "AAPL", "side": "sell",
        "quantity": "5", "order_type": "stop",
    }, format="json")
    assert r.status_code == 400
    assert "stop_price" in str(r.data)


def test_confirm_child_leg_rejected(api, paper_account):
    anchor = _bracket_rows(paper_account, "confirmed", "confirmed", "confirmed")
    child = anchor.child_legs.first()
    r = api.post(f"/api/broker/orders/{child.id}/confirm/", {}, format="json")
    assert r.status_code == 409
    assert r.data["code"] == "not_group_anchor"
