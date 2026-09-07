"""Adversarial review (reviewer: brokers) — idempotency / reconciliation proofs.

Each ``xfail(strict=True)`` test asserts the CORRECT behaviour and therefore
fails on the current code (reported as XFAIL) — that is the proof of the defect.
Plain tests prove a defect by asserting the wrong state directly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.brokers import reconcile as reconcile_mod
from apps.brokers.adapters import alpaca_paper as alpaca_mod
from apps.brokers.adapters.alpaca_paper import AlpacaPaperBroker
from apps.brokers.brackets import create_group, derive_group_status
from apps.brokers.credentials import set_api_key_secret
from apps.brokers.idempotency import (
    UNKNOWN_GRACE,
    resolve_unknown,
    submit_bracket_idempotent,
    submit_idempotent,
)
from apps.brokers.interfaces import BrokerTransientError
from apps.brokers.models import BrokerAccount, BrokerFill, BrokerOrder, BrokerSyncEvent
from apps.brokers.reconcile import poll_open_orders_for_account
from apps.brokers.tasks import poll_open_orders, sweep_stuck_confirmed_orders
from apps.portfolios.models import Portfolio

User = get_user_model()


# ---------------------------------------------------------------------------
# SDK stubs (same shape as tests/test_alpaca_brackets.py)
# ---------------------------------------------------------------------------


@dataclass
class StubOrder:
    id: str = "ord-1"
    client_order_id: str = "coid-1"
    symbol: str = "AAPL"
    side: str = "buy"
    qty: str = "10"
    order_type: str = "market"
    limit_price: str | None = None
    stop_price: str | None = None
    time_in_force: str = "day"
    status: str = "new"
    filled_qty: str = "0"
    filled_avg_price: str | None = None
    filled_at: datetime | None = None
    updated_at: datetime | None = None
    order_class: str = "simple"
    legs: list | None = None

    def model_dump(self) -> dict:
        return {"id": self.id, "status": self.status, "symbol": self.symbol}


@dataclass
class StubAccount:
    cash: str = "100000"
    buying_power: str = "200000"
    equity: str = "100000"
    currency: str = "USD"
    account_number: str = "PA-TEST"

    def model_dump(self) -> dict:
        return {"cash": self.cash}


@dataclass
class StubTradingClient:
    account: StubAccount = field(default_factory=StubAccount)
    positions: list = field(default_factory=list)
    orders: list = field(default_factory=list)
    by_id: dict[str, Any] = field(default_factory=dict)
    by_client_id: dict[str, Any] = field(default_factory=dict)
    submit_exception: Exception | None = None
    submit_response: Any = None
    calls: list = field(default_factory=list)

    def get_account(self):
        return self.account

    def get_all_positions(self):
        return self.positions

    def get_orders(self, filter=None):  # noqa: A002
        return self.orders

    def submit_order(self, order_data=None):
        self.calls.append(("submit_order", order_data))
        if self.submit_exception is not None:
            raise self.submit_exception
        if self.submit_response is not None:
            return self.submit_response
        return StubOrder(client_order_id=getattr(order_data, "client_order_id", ""))

    def get_order_by_id(self, order_id):
        return self.by_id.get(order_id) or StubOrder(id=order_id)

    def get_order_by_client_id(self, client_id):
        return self.by_client_id.get(client_id)

    def cancel_order_by_id(self, order_id):
        return None


def _apierror(status: int, body: str):
    from alpaca.common.exceptions import APIError

    class _R:
        status_code = status

    class _H:
        response = _R()
        request = None

    return APIError(body, http_error=_H())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def user(db):
    return User.objects.create_user(email="rv-brokers@example.com", password="x" * 12)


@pytest.fixture
def paper_account(user) -> BrokerAccount:
    p = Portfolio.objects.create(
        user=user, name="Broker · Alpaca paper", kind=Portfolio.KIND_BROKER,
        cash_balance=Decimal("100000"),
    )
    acc = BrokerAccount.objects.create(
        user=user, broker="alpaca_paper", mode="paper", account_id="PA-TEST",
        label="alpaca", portfolio=p, connection_status=BrokerAccount.STATUS_ACTIVE,
    )
    set_api_key_secret(acc, api_key="PK-TEST", api_secret="SK-TEST")
    return acc


@pytest.fixture
def stub_client() -> StubTradingClient:
    return StubTradingClient()


@pytest.fixture
def broker(paper_account, stub_client, monkeypatch) -> AlpacaPaperBroker:
    monkeypatch.setattr(alpaca_mod, "_make_client", lambda api_key, api_secret: stub_client)
    return AlpacaPaperBroker(paper_account)


def _confirmed(account, **kw) -> BrokerOrder:
    defaults = dict(
        broker_account=account, ticker="AAPL", side="buy", quantity=Decimal("10"),
        order_type="market", status=BrokerOrder.STATUS_CONFIRMED,
    )
    defaults.update(kw)
    return BrokerOrder.objects.create(**defaults)


# ---------------------------------------------------------------------------
# F: an order parked `unknown` after a network failure is NEVER resolved
# ---------------------------------------------------------------------------


def test_transient_submit_failure_parks_order_as_error_unknown(paper_account, broker, stub_client):
    stub_client.submit_exception = ConnectionError("read timed out")  # non-APIError → transient
    order = _confirmed(paper_account)
    with pytest.raises(BrokerTransientError):
        submit_idempotent(order=order, broker=broker)
    order.refresh_from_db()
    assert order.status == BrokerOrder.STATUS_ERROR
    assert order.idempotency_state == BrokerOrder.IDEM_UNKNOWN
    assert order.status not in BrokerOrder.OPEN_STATUSES  # ← the poll never sees it


def test_unknown_order_is_adopted_by_the_poll_when_alpaca_has_it(
    paper_account, broker, stub_client, monkeypatch,
):
    """FIXED (B3c): the poll now selects `idempotency_state=unknown` rows
    whatever their local status, and adopts the broker's answer."""
    order = _confirmed(paper_account)
    stub_client.submit_exception = ConnectionError("read timed out")
    with pytest.raises(BrokerTransientError):
        submit_idempotent(order=order, broker=broker)
    # Alpaca DID create the order (the request went through, the response was lost).
    filled = StubOrder(
        id="alp-1", client_order_id=str(order.client_order_id), status="filled",
        filled_qty="10", filled_avg_price="150",
    )
    stub_client.by_client_id[str(order.client_order_id)] = filled
    stub_client.by_id["alp-1"] = filled
    stub_client.orders = [filled]
    stub_client.submit_exception = None
    monkeypatch.setattr(reconcile_mod, "get_broker", lambda account: broker)

    poll_open_orders_for_account(paper_account)
    order.refresh_from_db()
    assert order.idempotency_state == BrokerOrder.IDEM_ACKNOWLEDGED
    assert order.broker_order_id == "alp-1"
    assert order.status == BrokerOrder.STATUS_FILLED
    assert order.error_message == ""
    # …and the fill reached the book, exactly once even across repeated polls.
    poll_open_orders_for_account(paper_account)
    assert paper_account.portfolio.positions.filter(
        ticker="AAPL", quantity=Decimal("10"),
    ).exists()


def test_unknown_order_is_invisible_to_the_beat_task_and_blocks_account_delete(
    paper_account, broker, stub_client, monkeypatch, user,
):
    """FIXED (B3c): the 30 s beat now scans the account, resolves the order
    against the venue and clears the block on deleting the account.

    Was: nothing in OPEN_STATUSES → the account was skipped entirely, the row
    stayed `error/unknown` forever, the sweep didn't reap it (it only targets
    `confirmed`) and the delete endpoint refused on the in-flight state."""
    order = _confirmed(paper_account)
    stub_client.submit_exception = ConnectionError("read timed out")
    with pytest.raises(BrokerTransientError):
        submit_idempotent(order=order, broker=broker)
    filled = StubOrder(
        id="alp-1", client_order_id=str(order.client_order_id), status="filled",
        filled_qty="10", filled_avg_price="150",
    )
    stub_client.by_client_id[str(order.client_order_id)] = filled
    stub_client.by_id["alp-1"] = filled
    stub_client.submit_exception = None
    monkeypatch.setattr(reconcile_mod, "get_broker", lambda account: broker)
    monkeypatch.setattr("apps.brokers.tasks.skip_when_offline", lambda *_a, **_k: False)

    summary = poll_open_orders()
    assert summary["accounts_scanned"] == 1          # `unknown` rows are pollable
    order.refresh_from_db()
    assert order.idempotency_state == BrokerOrder.IDEM_ACKNOWLEDGED
    assert order.status == BrokerOrder.STATUS_FILLED

    # Nothing for the stuck-confirmed sweep to do — the order is terminal.
    assert sweep_stuck_confirmed_orders(grace_hours=0) == 0

    client = APIClient()
    client.force_authenticate(user)
    resp = client.delete(f"/api/broker-accounts/{paper_account.pk}/")
    assert resp.status_code == 204, resp.content


def test_unknown_order_alpaca_never_heard_of_is_rejected_after_the_grace_window(
    paper_account, broker, stub_client, monkeypatch,
):
    """The other branch: Alpaca really did not get it. The poll re-tries it
    every cycle and only gives up once the grace window since the SUBMIT
    ATTEMPT has elapsed."""
    order = _confirmed(paper_account)
    stub_client.submit_exception = ConnectionError("read timed out")
    with pytest.raises(BrokerTransientError):
        submit_idempotent(order=order, broker=broker)
    stub_client.submit_exception = None
    monkeypatch.setattr(reconcile_mod, "get_broker", lambda account: broker)

    poll_open_orders_for_account(paper_account)   # still inside the grace window
    order.refresh_from_db()
    assert order.idempotency_state == BrokerOrder.IDEM_UNKNOWN
    assert order.status == BrokerOrder.STATUS_ERROR

    BrokerOrder.objects.filter(pk=order.pk).update(
        submit_attempted_at=timezone.now() - timedelta(hours=1),
    )
    poll_open_orders_for_account(paper_account)
    order.refresh_from_db()
    assert order.status == BrokerOrder.STATUS_REJECTED


# ---------------------------------------------------------------------------
# F: resolve_unknown's grace window is measured from created_at, not from the
#    submit attempt — a held (pending_open) order that fails at release is
#    marked REJECTED on the very first reconcile pass.
# ---------------------------------------------------------------------------


def test_resolve_unknown_rejects_old_orders_immediately(paper_account, broker, stub_client):
    """FIXED (B3c): the grace window runs from `submit_attempted_at`, not
    `created_at`, so a Friday-drafted order released on Tuesday gets its full
    window instead of being declared rejected on the first reconcile pass."""
    order = _confirmed(paper_account)
    # Created Friday, released Monday: created_at is far older than UNKNOWN_GRACE.
    BrokerOrder.objects.filter(pk=order.pk).update(
        created_at=timezone.now() - timedelta(days=2),
    )
    order.refresh_from_db()
    stub_client.submit_exception = ConnectionError("read timed out")
    with pytest.raises(BrokerTransientError):
        submit_idempotent(order=order, broker=broker)
    order.refresh_from_db()
    assert order.submit_attempted_at is not None
    assert order.submit_attempted_at > order.created_at
    # Alpaca is still indexing the order (lookup returns 404 for now).
    stub_client.by_client_id.clear()
    assert resolve_unknown(order, broker) is None      # seconds after the attempt
    order.refresh_from_db()
    assert order.status == BrokerOrder.STATUS_ERROR
    assert order.idempotency_state == BrokerOrder.IDEM_UNKNOWN

    # Once the window really has elapsed since the ATTEMPT, it fails closed.
    BrokerOrder.objects.filter(pk=order.pk).update(
        submit_attempted_at=timezone.now() - UNKNOWN_GRACE - timedelta(seconds=1),
    )
    order.refresh_from_db()
    assert resolve_unknown(order, broker) == BrokerOrder.STATUS_REJECTED
    order.refresh_from_db()
    assert order.status == BrokerOrder.STATUS_REJECTED


def test_stuck_confirmed_sweep_measures_grace_from_the_submit_attempt(paper_account):
    """The same clock change in the sweeper: an order drafted days ago but
    only just handed to the broker is not 'stuck'."""
    order = _confirmed(paper_account, error_message="transient blip")
    BrokerOrder.objects.filter(pk=order.pk).update(
        created_at=timezone.now() - timedelta(days=3),
        submit_attempted_at=timezone.now(),
    )
    assert sweep_stuck_confirmed_orders(grace_hours=24) == 0
    order.refresh_from_db()
    assert order.status == BrokerOrder.STATUS_CONFIRMED

    # Genuinely old attempt → reaped.
    BrokerOrder.objects.filter(pk=order.pk).update(
        submit_attempted_at=timezone.now() - timedelta(days=3),
    )
    assert sweep_stuck_confirmed_orders(grace_hours=24) == 1
    order.refresh_from_db()
    assert order.status == BrokerOrder.STATUS_REJECTED


# ---------------------------------------------------------------------------
# F: a 422 whose body is not {"message": …} JSON escapes the translator
#    (same shape as the e8c318f KeyError bug, one function over) and leaves
#    the order permanently `submit_pending`.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "<html><body>422 Unprocessable Entity</body></html>",   # non-JSON body
        json.dumps({"code": 42210000}),                          # JSON without "message"
    ],
    ids=["non-json", "json-no-message"],
)
def test_422_without_message_key_crashes_duplicate_check_and_strands_order(
    paper_account, broker, stub_client, body, user,
):
    """FIXED (B3c): the translator never raises on a body it cannot read.

    Was: `_is_duplicate_client_order_id` / `_alpaca_error_code` read
    `APIError.message` / `.code`, both of which json.loads the body, so a
    proxy's HTML page or a message-less payload raised JSONDecodeError/KeyError
    out of `_do_submit` — before either arm of the submit state machine could
    run — leaving the order in submit_pending/confirmed with an empty
    error_message forever.

    Now the two bodies get the two correct, DIFFERENT classifications:
      * unparseable (no Alpaca code) → transient: we don't know what the venue
        did, so park it `unknown` for the poll to resolve;
      * a real Alpaca error code    → definitive rejection.
    """
    from apps.brokers.interfaces import BrokerError

    stub_client.submit_exception = _apierror(422, body)
    order = _confirmed(paper_account)
    has_alpaca_code = "code" in body

    expected = BrokerError if has_alpaca_code else BrokerTransientError
    with pytest.raises(expected):
        submit_idempotent(order=order, broker=broker)

    order.refresh_from_db()
    assert order.error_message != ""                 # the failure is recorded
    if has_alpaca_code:
        assert order.idempotency_state == BrokerOrder.IDEM_UNSUBMITTED
        assert order.status == BrokerOrder.STATUS_REJECTED
    else:
        assert order.idempotency_state == BrokerOrder.IDEM_UNKNOWN
        assert order.status == BrokerOrder.STATUS_ERROR
        # The poll picks the unknown row up and adopts the venue's answer.
        stub_client.submit_exception = None
        adopted = StubOrder(
            id="alp-9", client_order_id=str(order.client_order_id), status="canceled",
        )
        stub_client.by_client_id[str(order.client_order_id)] = adopted
        stub_client.by_id["alp-9"] = adopted
        monkeypatch_free_poll(paper_account, broker)
        order.refresh_from_db()
        assert order.idempotency_state == BrokerOrder.IDEM_ACKNOWLEDGED
        assert order.broker_order_id == "alp-9"

    # Either way the row is terminal, so the account deletes cleanly.
    client = APIClient()
    client.force_authenticate(user)
    assert client.delete(f"/api/broker-accounts/{paper_account.pk}/").status_code == 204


def monkeypatch_free_poll(account, broker):
    """Run one account poll with an explicit broker (no monkeypatch needed)."""
    import unittest.mock as um

    with um.patch.object(reconcile_mod, "get_broker", lambda _a: broker):
        poll_open_orders_for_account(account)


def test_422_with_proper_message_is_translated_to_rejection(paper_account, broker, stub_client):
    """Control: the well-formed 422 path works (so the defect is only the shape)."""
    stub_client.submit_exception = _apierror(
        422, json.dumps({"code": 42210000, "message": "insufficient buying power"}),
    )
    order = _confirmed(paper_account)
    from apps.brokers.interfaces import BrokerError

    with pytest.raises(BrokerError):
        submit_idempotent(order=order, broker=broker)
    order.refresh_from_db()
    assert order.status == BrokerOrder.STATUS_REJECTED
    assert order.idempotency_state == BrokerOrder.IDEM_UNSUBMITTED


# ---------------------------------------------------------------------------
# F: bracket protective legs stop being polled once the entry fills
# ---------------------------------------------------------------------------


def _bracket(account) -> BrokerOrder:
    anchor = create_group(
        account=account, decision=None, ticker="AAPL", side="buy",
        quantity=Decimal("10"), time_in_force="gtc", order_class="bracket",
        entry_order_type="limit", entry_limit_price=Decimal("100"),
        take_profit_limit_price=Decimal("110"), stop_loss_stop_price=Decimal("95"),
    )
    BrokerOrder.objects.filter(group_id=anchor.group_id).update(
        status=BrokerOrder.STATUS_CONFIRMED,
    )
    anchor.refresh_from_db()
    return anchor


def _parent(coid, *, entry_status, tp_status, sl_status, entry_filled="0", sl_filled="0"):
    return StubOrder(
        id="entry-1", client_order_id=coid, symbol="AAPL", side="buy", qty="10",
        order_type="limit", order_class="bracket", status=entry_status,
        filled_qty=entry_filled, filled_avg_price="100" if entry_filled != "0" else None,
        legs=[
            StubOrder(id="tp-1", symbol="AAPL", side="sell", order_type="limit",
                      limit_price="110", status=tp_status, filled_qty="0"),
            StubOrder(id="sl-1", symbol="AAPL", side="sell", order_type="stop",
                      stop_price="95", status=sl_status, filled_qty=sl_filled,
                      filled_avg_price="95" if sl_filled != "0" else None),
        ],
    )


def test_bracket_exit_fill_observed_after_entry_filled_on_an_earlier_poll(
    paper_account, broker, stub_client, monkeypatch,
):
    """FIXED (B3c): an anchor whose protective legs are still working stays in
    the poll after its own entry has filled, so the stop-loss fill is seen."""
    monkeypatch.setattr(reconcile_mod, "get_broker", lambda account: broker)
    anchor = _bracket(paper_account)
    coid = str(anchor.client_order_id)
    stub_client.submit_response = _parent(
        coid, entry_status="new", tp_status="held", sl_status="held",
    )
    submit_bracket_idempotent(anchor=anchor, broker=broker)

    now = datetime.now(tz=UTC)
    # Poll 1 (T+30 s): entry filled, both exits now live at the venue.
    stub_client.by_id["entry-1"] = _parent(
        coid, entry_status="filled", tp_status="new", sl_status="new", entry_filled="10",
    )
    stub_client.orders = [
        StubOrder(id="entry-1", symbol="AAPL", side="buy", filled_qty="10",
                  filled_avg_price="100", status="filled", filled_at=now + timedelta(minutes=1)),
    ]
    poll_open_orders_for_account(paper_account)
    anchor.refresh_from_db()
    sl = anchor.child_legs.get(leg_role=BrokerOrder.LEG_STOP_LOSS)
    assert anchor.status == BrokerOrder.STATUS_FILLED
    assert sl.status == BrokerOrder.STATUS_SUBMITTED

    # Poll 2 (hours later): the stop-loss fired at the venue.
    stub_client.by_id["entry-1"] = _parent(
        coid, entry_status="filled", tp_status="canceled", sl_status="filled",
        entry_filled="10", sl_filled="10",
    )
    stub_client.orders.append(
        StubOrder(id="sl-1", symbol="AAPL", side="sell", filled_qty="10",
                  filled_avg_price="95", status="filled", filled_at=now + timedelta(hours=2)),
    )
    poll_open_orders_for_account(paper_account)

    sl.refresh_from_db()
    assert sl.status == BrokerOrder.STATUS_FILLED
    assert derive_group_status(anchor) == "closed"
    assert not paper_account.portfolio.positions.filter(ticker="AAPL").exists()


def test_bracket_legs_are_orphaned_after_entry_fill(
    paper_account, broker, stub_client, monkeypatch,
):
    """FIXED (B3c): same scenario from the other side — the second poll DOES
    reach the venue, and the leg fill is applied exactly once however many
    times the poll runs.

    Was: no venue call at all on poll 2; the stop-loss stayed `submitted`
    forever, the group read "working" on a flat book and the position was
    never closed."""
    monkeypatch.setattr(reconcile_mod, "get_broker", lambda account: broker)
    anchor = _bracket(paper_account)
    coid = str(anchor.client_order_id)
    stub_client.submit_response = _parent(
        coid, entry_status="new", tp_status="held", sl_status="held",
    )
    submit_bracket_idempotent(anchor=anchor, broker=broker)
    now = datetime.now(tz=UTC)
    stub_client.by_id["entry-1"] = _parent(
        coid, entry_status="filled", tp_status="new", sl_status="new", entry_filled="10",
    )
    stub_client.orders = [
        StubOrder(id="entry-1", symbol="AAPL", side="buy", filled_qty="10",
                  filled_avg_price="100", status="filled", filled_at=now + timedelta(minutes=1)),
    ]
    poll_open_orders_for_account(paper_account)
    stub_client.by_id["entry-1"] = _parent(
        coid, entry_status="filled", tp_status="canceled", sl_status="filled",
        entry_filled="10", sl_filled="10",
    )
    stub_client.orders.append(
        StubOrder(id="sl-1", symbol="AAPL", side="sell", filled_qty="10",
                  filled_avg_price="95", status="filled", filled_at=now + timedelta(hours=2)),
    )
    # Count the venue reads of poll 2 (the stub only records writes by default).
    reads: list[str] = []
    inner_get = stub_client.get_order_by_id
    stub_client.get_order_by_id = lambda order_id: (
        reads.append(order_id) or inner_get(order_id)
    )
    poll_open_orders_for_account(paper_account)
    assert reads == ["entry-1"]      # the anchor IS re-read, once, on poll 2
    sl = anchor.child_legs.get(leg_role=BrokerOrder.LEG_STOP_LOSS)
    assert sl.status == BrokerOrder.STATUS_FILLED
    assert derive_group_status(anchor) == "closed"
    assert not paper_account.portfolio.positions.filter(ticker="AAPL").exists()

    # Exactly-once: re-polling the same closed group books nothing further.
    fills_before = BrokerFill.objects.filter(order=sl).count()
    ledger_before = paper_account.portfolio.ledger.count()
    cash_before = Portfolio.objects.get(pk=paper_account.portfolio_id).cash_balance
    for _ in range(3):
        poll_open_orders_for_account(paper_account)
    assert BrokerFill.objects.filter(order=sl).count() == fills_before
    assert paper_account.portfolio.ledger.count() == ledger_before
    assert Portfolio.objects.get(pk=paper_account.portfolio_id).cash_balance == cash_before


# ---------------------------------------------------------------------------
# F: BrokerTransientError is not a BrokerError → reconcile_account leaks it,
#    the manual Sync endpoint 500s and a BrokerSyncEvent is left unfinished.
# ---------------------------------------------------------------------------


def test_manual_sync_500s_on_transient_broker_error(paper_account, user, monkeypatch):
    """FIXED (B3c): `reconcile_account` classifies a transient failure instead
    of leaking it. BrokerTransientError does NOT subclass BrokerError, so it
    escaped every arm: the endpoint 500'd and the sync event was left dangling
    "in progress" forever."""
    last_synced_before = paper_account.last_synced_at

    class _Down:
        def get_positions(self):
            raise BrokerTransientError("Alpaca get_all_positions failed: timeout")

        def get_account(self):
            raise BrokerTransientError("Alpaca get_account failed: timeout")

    monkeypatch.setattr(reconcile_mod, "get_broker", lambda account: _Down())
    client = APIClient()
    client.force_authenticate(user)
    client.raise_request_exception = False
    resp = client.post(f"/api/broker-accounts/{paper_account.pk}/sync/")
    assert resp.status_code == 200, resp.content
    assert resp.json()["drift_detected"] is False
    ev = BrokerSyncEvent.objects.get(broker_account=paper_account)
    assert ev.finished_at is not None
    assert "transient" in ev.error_message
    # State unchanged: nothing written, nothing marked synced. The beat retries.
    paper_account.refresh_from_db()
    assert paper_account.last_synced_at == last_synced_before
    assert ev.ledger_entries_written == 0


# ---------------------------------------------------------------------------
# Positive proof: the Alpaca client is bound to the paper host by construction
# ---------------------------------------------------------------------------


def test_make_client_is_pinned_to_the_paper_host():
    client = alpaca_mod._make_client(api_key="AKLIVEKEY", api_secret="secret")
    assert client._base_url == "https://paper-api.alpaca.markets"
    assert client._sandbox is True


def test_request_opts_carry_no_timeout():
    """alpaca-py never passes `timeout=` to requests: a hung socket blocks the
    Celery worker / gunicorn worker indefinitely.

    FIXED (B3c) by wrapping the SDK client's own requests Session, since the
    SDK gives us no timeout knob — this test now pins BOTH halves: the SDK
    still has no timeout of its own, and our client injects one anyway."""
    import inspect

    from alpaca.common import rest

    src = (
        inspect.getsource(rest.RESTClient._request)
        + inspect.getsource(rest.RESTClient._one_request)
    )
    assert "timeout" not in src

    seen: dict = {}

    class _Session:
        def request(self, method, url, **kwargs):
            seen.update(kwargs)
            return None

    class _Client:
        _session = _Session()

    client = alpaca_mod._apply_http_timeout(_Client())
    client._session.request("GET", "/v2/account")
    assert seen["timeout"] == (10.0, 30.0)          # (connect, read) seconds
    assert alpaca_mod.HTTP_TIMEOUT == (10.0, 30.0)


def test_real_client_session_carries_the_timeout():
    """End-to-end: the timeout reaches the actual requests transport of the
    real TradingClient, not just a stub."""
    client = alpaca_mod._make_client(api_key="AKTEST", api_secret="secret")
    seen: dict = {}

    class _Resp:
        status_code = 200
        text = ""

        def raise_for_status(self):
            return None

    def _fake_send(_prepared, **kwargs):
        seen.update(kwargs)
        return _Resp()

    client._session.send = _fake_send      # nothing leaves the process
    client._session.request("GET", "https://paper-api.alpaca.markets/v2/account")
    assert seen["timeout"] == (10.0, 30.0)

    # Re-applying the wrapper is a no-op (no stacking on reconnect).
    wrapper = client._session.request
    alpaca_mod._apply_http_timeout(client)
    assert client._session.request is wrapper
