"""IBKRBroker adapter unit tests (P3a-2).

These tests cover the adapter's request/response translation against
canned IBKR responses. The canned response shapes are based on IBKR's
published Client Portal Web API docs (see ADR 0011 §3 and the agent-
verified shapes in phase-03a-2-paper-trading-ibkr.md Risks #7).

Real VCR cassettes recorded against a live IBKR paper gateway land
during the manual paper-account integration run (Task #22 / Risks #7).
When they do, they replace or complement the canned responses here —
the test boundaries (one test per Broker method + reply-loop variants
+ idempotency variants) stay the same.

Pattern:
- Each test installs a `StubGatewaySession` via monkeypatch.
- The stub returns canned dicts/lists for the endpoints the adapter
  calls. Unknown endpoints raise AssertionError so an accidentally-
  added HTTP call doesn't slip past silently.
- The adapter is constructed against a real BrokerAccount row but the
  HTTP layer never runs.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from apps.brokers.adapters import ibkr as ibkr_module
from apps.brokers.adapters.ibkr import (
    DUPLICATE_COID_PATTERNS,
    REPLY_LOOP_ITERATION_CAP,
    IBKRBroker,
    _map_status,
)
from apps.brokers.interfaces import (
    BrokerError,
    OrderTicket,
)
from apps.brokers.models import BrokerAccount
from apps.portfolios.models import Portfolio

User = get_user_model()


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------


class StubGatewaySession:
    """Replaces IBKRGatewaySession inside the IBKR adapter. Each test sets
    canned responses on the instance and (optionally) inspects `calls`.

    `responses` maps either:
      - (method, path)  → response value, or
      - path            → response value (any method)
    Values that are Exception instances are raised; callables are invoked
    once per call (so they can return different values across calls);
    anything else is returned verbatim.
    """

    def __init__(self, *a, **kw) -> None:
        self.responses: dict = {}
        self.calls: list[tuple] = []

    def __enter__(self) -> StubGatewaySession:
        return self

    def __exit__(self, *exc) -> None:
        return None

    def close(self) -> None:
        return None

    def _resolve(self, method: str, path: str, *, json=None, params=None):
        self.calls.append((method, path, json, params))
        key = (method, path)
        if key in self.responses:
            value = self.responses[key]
        elif path in self.responses:
            value = self.responses[path]
        else:
            raise AssertionError(
                f"StubGatewaySession: unhandled {method} {path} "
                f"(set responses[(method, path)] or responses[path] first)",
            )
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return value()
        return value

    def request(self, method, path, *, json=None, params=None):
        return self._resolve(method, path, json=json, params=params)

    def get(self, path, **kw):
        return self._resolve("GET", path, **kw)

    def post(self, path, **kw):
        return self._resolve("POST", path, **kw)

    def delete(self, path, **kw):
        return self._resolve("DELETE", path, **kw)

    # Convenience wrappers — same shape as the real IBKRGatewaySession
    # for completeness; tests rarely call these directly.

    def tickle(self):
        return self.post("/tickle")

    def auth_status(self):
        r = self.post("/iserver/auth/status", json={})
        return r if isinstance(r, dict) else {}

    def reply(self, reply_id, *, confirmed):
        return self.post(f"/iserver/reply/{reply_id}", json={"confirmed": confirmed})


@pytest.fixture
def user(db):
    return User.objects.create_user(email="ibkr-test@example.com", password="x" * 12)


@pytest.fixture
def account(user) -> BrokerAccount:
    """A connected paper IBKR account — account_id matches the DU-prefix
    convention (Risks #6)."""
    portfolio = Portfolio.objects.create(
        user=user, name="Broker · IBKR paper", kind=Portfolio.KIND_BROKER,
        cash_balance=Decimal("0"),
    )
    return BrokerAccount.objects.create(
        user=user, broker="ibkr", mode="paper",
        account_id="DU1234567", label="IBKR paper test",
        portfolio=portfolio,
        connection_status=BrokerAccount.STATUS_ACTIVE,
    )


@pytest.fixture
def stub(monkeypatch) -> StubGatewaySession:
    """Install StubGatewaySession in the IBKR adapter module. The shared
    instance is returned so each test can populate `responses` and assert
    against `calls`."""
    session = StubGatewaySession()
    monkeypatch.setattr(ibkr_module, "IBKRGatewaySession", lambda *a, **kw: session)
    return session


@pytest.fixture
def broker(account, stub) -> IBKRBroker:
    """An IBKRBroker bound to the stub session. Order matters — the stub
    fixture must install the monkeypatch before IBKRBroker.__init__ runs."""
    return IBKRBroker(account)


def _ticket(
    *,
    client_order_id: str = "test-cid-1",
    ticker: str = "AAPL",
    side: str = "buy",
    quantity: Decimal = Decimal("10"),
    order_type: str = "limit",
    limit_price: Decimal | None = Decimal("100"),
    time_in_force: str = "day",
) -> OrderTicket:
    return OrderTicket(
        client_order_id=client_order_id,
        ticker=ticker, side=side, quantity=quantity,
        order_type=order_type, limit_price=limit_price,
        time_in_force=time_in_force,
    )


# ---------------------------------------------------------------------------
# get_account
# ---------------------------------------------------------------------------


def test_get_account_maps_summary_fields(broker, stub):
    stub.responses["/portfolio/DU1234567/summary"] = {
        "totalcashvalue": {"amount": 50000.0, "currency": "USD"},
        "buyingpower": {"amount": 100000.0, "currency": "USD"},
        "netliquidation": {"amount": 75000.0, "currency": "USD"},
    }
    snap = broker.get_account()
    assert snap.account_id == "DU1234567"
    assert snap.cash == Decimal("50000.0")
    assert snap.buying_power == Decimal("100000.0")
    assert snap.equity == Decimal("75000.0")
    assert snap.currency == "USD"
    # The call went exactly where we expected.
    assert ("GET", "/portfolio/DU1234567/summary", None, None) in stub.calls


def test_get_account_falls_back_to_availablefunds(broker, stub):
    """When `totalcashvalue` is missing, the adapter falls back to
    `availablefunds`. Some IBKR account types expose only one of them."""
    stub.responses["/portfolio/DU1234567/summary"] = {
        "availablefunds": {"amount": 33000.0, "currency": "USD"},
        "buyingpower": {"amount": 60000.0, "currency": "USD"},
    }
    snap = broker.get_account()
    assert snap.cash == Decimal("33000.0")


# ---------------------------------------------------------------------------
# get_positions
# ---------------------------------------------------------------------------


def test_get_positions_paginates_until_short_page(broker, stub):
    # Page 0: 30 rows (full page) — the adapter will fetch page 1 too.
    page0 = [
        {"ticker": f"T{i:02d}", "position": 1, "avgCost": 100.0}
        for i in range(30)
    ]
    # Page 1: 2 rows — short page, no further fetches.
    page1 = [
        {"ticker": "AAPL", "position": 5, "avgCost": 150.0},
        {"ticker": "MSFT", "position": 3, "avgCost": 300.0},
    ]
    stub.responses[("GET", "/portfolio/DU1234567/positions/0")] = page0
    stub.responses[("GET", "/portfolio/DU1234567/positions/1")] = page1
    positions = broker.get_positions()
    # 30 + 2 = 32. (None of the test rows have qty=0 so none get filtered.)
    assert len(positions) == 32
    # Pagination stopped after page 1 — page 2 wasn't fetched.
    assert ("GET", "/portfolio/DU1234567/positions/2", None, None) not in stub.calls


def test_get_positions_skips_zero_quantity_rows(broker, stub):
    """IBKR returns position=0 rows for recently-closed positions. We don't
    want those in the snapshot — they pollute reconciliation drift."""
    stub.responses[("GET", "/portfolio/DU1234567/positions/0")] = [
        {"ticker": "AAPL", "position": 10, "avgCost": 150.0},
        {"ticker": "STALE", "position": 0, "avgCost": 200.0},
    ]
    positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].ticker == "AAPL"


def test_get_positions_preserves_short_sign(broker, stub):
    """ADR 0011: positions returns are signed — negative = short. The
    adapter must NOT take the absolute value."""
    stub.responses[("GET", "/portfolio/DU1234567/positions/0")] = [
        {"ticker": "TSLA", "position": -25, "avgCost": 200.0},
    ]
    positions = broker.get_positions()
    assert positions[0].quantity == Decimal("-25")


# ---------------------------------------------------------------------------
# get_recent_fills
# ---------------------------------------------------------------------------


def _epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def test_get_recent_fills_filters_by_since(broker, stub):
    now = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)
    yesterday = now - timedelta(days=1)
    two_days_ago = now - timedelta(days=2)
    stub.responses[("GET", "/iserver/account/trades")] = [
        {
            "execution_id": "exec-recent",
            "orderId": "100",
            "symbol": "AAPL",
            "side": "B",
            "size": 5,
            "price": 150.0,
            "trade_time_r": _epoch_ms(yesterday),
        },
        {
            "execution_id": "exec-old",
            "orderId": "99",
            "symbol": "AAPL",
            "side": "B",
            "size": 3,
            "price": 145.0,
            "trade_time_r": _epoch_ms(two_days_ago),
        },
    ]
    fills = broker.get_recent_fills(since=now - timedelta(hours=30))
    # The 30-hour cutoff includes yesterday but excludes two-days-ago.
    assert len(fills) == 1
    assert fills[0].broker_fill_id == "exec-recent"
    assert fills[0].quantity == Decimal("5")
    assert fills[0].side == "buy"
    # ?days=7 was requested.
    assert stub.calls[0] == ("GET", "/iserver/account/trades", None, {"days": 7})


def test_get_recent_fills_maps_sell_side(broker, stub):
    """IBKR encodes sells as side="S" (or sometimes "SLD"). The adapter
    maps both to side="sell"."""
    now = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)
    stub.responses[("GET", "/iserver/account/trades")] = [
        {
            "execution_id": "exec-1",
            "orderId": "1",
            "symbol": "AAPL",
            "side": "S",
            "size": 2,
            "price": 200.0,
            "trade_time_r": _epoch_ms(now),
        },
        {
            "execution_id": "exec-2",
            "orderId": "2",
            "symbol": "AAPL",
            "side": "SLD",
            "size": 1,
            "price": 201.0,
            "trade_time_r": _epoch_ms(now),
        },
    ]
    fills = broker.get_recent_fills(since=now - timedelta(hours=1))
    assert {f.side for f in fills} == {"sell"}


def test_get_recent_fills_filters_other_accounts(broker, stub):
    """Defensive — if IBKR returns trades from a different account_id (it
    shouldn't, since the session is per-account, but the trades endpoint
    doesn't take an account filter), the adapter ignores them."""
    now = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)
    stub.responses[("GET", "/iserver/account/trades")] = [
        {
            "execution_id": "ours",
            "orderId": "1", "symbol": "AAPL", "side": "B",
            "size": 1, "price": 100.0,
            "trade_time_r": _epoch_ms(now),
            "account": "DU1234567",
        },
        {
            "execution_id": "theirs",
            "orderId": "2", "symbol": "AAPL", "side": "B",
            "size": 1, "price": 100.0,
            "trade_time_r": _epoch_ms(now),
            "account": "DU9999999",
        },
    ]
    fills = broker.get_recent_fills(since=now - timedelta(hours=1))
    assert len(fills) == 1
    assert fills[0].broker_fill_id == "ours"


# ---------------------------------------------------------------------------
# submit_order — happy path
# ---------------------------------------------------------------------------


def _setup_conid_for(stub, ticker: str, conid: int = 265598):
    """Helper — wires the secdef/search response so conid resolution
    returns `conid` for `ticker`."""
    stub.responses[("GET", "/iserver/secdef/search")] = [
        {
            "conid": conid,
            "symbol": ticker,
            "sections": [{"secType": "STK"}],
        },
    ]


def test_submit_order_clean_acceptance(broker, stub):
    _setup_conid_for(stub, "AAPL")
    stub.responses[("POST", "/iserver/account/DU1234567/orders")] = [
        {"order_id": "ibkr-order-1", "order_status": "Submitted"},
    ]
    snap = broker.submit_order(_ticket())
    assert snap.broker_order_id == "ibkr-order-1"
    assert snap.client_order_id == "test-cid-1"
    assert snap.status == "submitted"
    # No reply-loop messages on the clean path.
    assert "reply_messages" not in snap.raw


def test_submit_order_sends_cid_as_cOID(broker, stub):
    """ADR 0011 §3: the framework client_order_id maps to IBKR's cOID."""
    _setup_conid_for(stub, "AAPL")
    stub.responses[("POST", "/iserver/account/DU1234567/orders")] = [
        {"order_id": "1", "order_status": "Submitted"},
    ]
    broker.submit_order(_ticket(client_order_id="my-coid-abc"))
    submit_call = next(
        c for c in stub.calls
        if c[0] == "POST" and c[1] == "/iserver/account/DU1234567/orders"
    )
    body = submit_call[2]
    assert body["orders"][0]["cOID"] == "my-coid-abc"
    assert body["orders"][0]["side"] == "BUY"
    assert body["orders"][0]["orderType"] == "LMT"
    assert body["orders"][0]["price"] == 100.0


def test_submit_order_market_omits_price(broker, stub):
    _setup_conid_for(stub, "AAPL")
    stub.responses[("POST", "/iserver/account/DU1234567/orders")] = [
        {"order_id": "1", "order_status": "Submitted"},
    ]
    broker.submit_order(_ticket(order_type="market", limit_price=None))
    submit_call = next(
        c for c in stub.calls
        if c[0] == "POST" and c[1] == "/iserver/account/DU1234567/orders"
    )
    body = submit_call[2]["orders"][0]
    assert body["orderType"] == "MKT"
    assert "price" not in body


# ---------------------------------------------------------------------------
# submit_order — reply loop (ADR 0011 §3)
# ---------------------------------------------------------------------------


def test_submit_order_blocking_reply_returns_rejected(broker, stub):
    """v1 allowlist is empty — every reply-loop message blocks the
    order and routes to status='rejected' with raw['reply_messages']
    preserved verbatim."""
    _setup_conid_for(stub, "AAPL")
    # First call: orders → returns a reply-loop prompt.
    stub.responses[("POST", "/iserver/account/DU1234567/orders")] = [
        {
            "id": "reply-uuid-1",
            "message": "Order size exceeds 1% of average daily volume.",
            "messageIds": ["o354"],
        },
    ]
    snap = broker.submit_order(_ticket())
    assert snap.status == "rejected"
    assert snap.raw["error"] == "reply_loop_blocking_message"
    assert len(snap.raw["reply_messages"]) == 1
    msg = snap.raw["reply_messages"][0]
    assert msg["messageId"] == "o354"
    assert "average daily volume" in msg["message"]


def test_submit_order_reply_loop_iteration_cap(broker, stub, monkeypatch):
    """If IBKR keeps prompting beyond the iteration cap, the adapter
    bails out with status='rejected' and error=reply_loop_iteration_cap.

    The cap is only reachable when the allowlist auto-confirms — with
    the v1 empty allowlist, the first blocking message ends the loop
    instead. We temporarily populate the allowlist so messages auto-
    confirm, then have IBKR return a new message each iteration so the
    loop never terminates organically. This proves the cap is a real
    safety net.
    """
    import re as _re
    monkeypatch.setattr(
        ibkr_module,
        "REPLY_ALLOWLIST",
        (("o999", _re.compile(r"A new warning")),),
    )
    _setup_conid_for(stub, "AAPL")

    def make_prompt():
        return [{
            "id": "reply-uuid",
            "message": "A new warning.",
            "messageIds": ["o999"],
        }]

    # Every POST (orders + reply/*) returns a prompt — the loop never
    # terminates organically.
    stub.responses[("POST", "/iserver/account/DU1234567/orders")] = make_prompt
    stub.responses["/iserver/reply/reply-uuid"] = make_prompt

    snap = broker.submit_order(_ticket())
    assert snap.status == "rejected"
    assert snap.raw["error"] == "reply_loop_iteration_cap"
    # Each iteration appends one message; the cap is the message count.
    assert len(snap.raw["reply_messages"]) == REPLY_LOOP_ITERATION_CAP


def test_reply_allowlist_is_empty_in_v1():
    """ADR 0011 §3 explicit invariant: v1 ships with an empty allowlist.
    Populated empirically during the manual paper-account run."""
    from apps.brokers.adapters.ibkr import REPLY_ALLOWLIST
    assert REPLY_ALLOWLIST == ()


# ---------------------------------------------------------------------------
# submit_order — duplicate-cOID resolution (Risks #7c)
# ---------------------------------------------------------------------------


def test_submit_order_duplicate_coid_resolves_via_find(broker, stub):
    """ADR 0011 §3: a duplicate-cOID 4xx from IBKR is treated as evidence
    that the first order already exists. The adapter calls
    find_order_by_client_id (which queries /iserver/account/orders) and
    returns the pre-existing order — no duplicate is created."""
    _setup_conid_for(stub, "AAPL")
    stub.responses[("POST", "/iserver/account/DU1234567/orders")] = BrokerError(
        "gateway POST /iserver/account/DU1234567/orders: HTTP 400: duplicate cOID",
    )
    # The fallback path queries /iserver/account/orders.
    stub.responses[("GET", "/iserver/account/orders")] = {
        "orders": [
            {
                "orderId": "first-order-id",
                "cOID": "test-cid-1",
                "status": "Submitted",
                "side": "BUY",
                "ticker": "AAPL",
                "totalSize": 10,
                "filledQuantity": 0,
                "orderType": "LMT",
                "price": 100.0,
                "tif": "DAY",
            },
        ],
    }
    snap = broker.submit_order(_ticket())
    assert snap.broker_order_id == "first-order-id"
    assert snap.client_order_id == "test-cid-1"
    assert snap.status == "submitted"


def test_submit_order_generic_4xx_propagates(broker, stub):
    """A 4xx that doesn't look like a duplicate-cOID error is surfaced —
    it could be a malformed order, an unsupported symbol, etc."""
    _setup_conid_for(stub, "AAPL")
    stub.responses[("POST", "/iserver/account/DU1234567/orders")] = BrokerError(
        "gateway POST .../orders: HTTP 400: insufficient buying power",
    )
    # Make /iserver/account/orders unreachable so the fallback doesn't pretend.
    # (It shouldn't be queried at all for this error shape, but assert that
    # by leaving it unset — stub will raise AssertionError if called.)
    with pytest.raises(BrokerError, match="insufficient buying power"):
        broker.submit_order(_ticket())


def test_duplicate_coid_patterns_match_known_phrases():
    """The DUPLICATE_COID_PATTERNS list is pinned in the adapter; this
    test asserts the regexes match the shapes we expect. The manual
    paper run captures the real error and adds/refines patterns."""
    test_phrases = [
        "duplicate cOID",
        "cOID duplicate",
        "duplicate COID",  # case-insensitive
        "cOID already used",
    ]
    for phrase in test_phrases:
        assert any(p.search(phrase) for p in DUPLICATE_COID_PATTERNS), (
            f"no pattern matched {phrase!r}"
        )
    # Non-duplicate errors don't match.
    assert not any(
        p.search("insufficient buying power")
        for p in DUPLICATE_COID_PATTERNS
    )


# ---------------------------------------------------------------------------
# get_order
# ---------------------------------------------------------------------------


def test_get_order_via_orders_endpoint(broker, stub):
    stub.responses[("GET", "/iserver/account/orders")] = {
        "orders": [
            {
                "orderId": "id-100",
                "cOID": "cid-100",
                "status": "Submitted",
                "ticker": "AAPL",
                "side": "BUY",
                "totalSize": 10,
                "filledQuantity": 0,
                "orderType": "LMT",
                "price": 150.0,
                "tif": "DAY",
            },
        ],
    }
    snap = broker.get_order("id-100")
    assert snap.broker_order_id == "id-100"
    assert snap.status == "submitted"
    assert snap.quantity == Decimal("10")


def test_get_order_falls_back_to_trades_for_terminal_orders(broker, stub, monkeypatch):
    """Risks #7b: terminal orders may roll off /iserver/account/orders
    quickly. The adapter falls back to /iserver/account/trades to derive
    the final status."""
    now = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)
    # The fallback derives its window from timezone.now() - 7d (ibkr.py:485), so
    # against the real clock the fixed-date fixture trade rolls outside the window
    # once wall-time passes now+7d, making this test self-expire. Freeze now to
    # the fixture's frame so the window is deterministic.
    monkeypatch.setattr("apps.brokers.adapters.ibkr.timezone.now", lambda: now)
    # /orders is empty (the order rolled off).
    stub.responses[("GET", "/iserver/account/orders")] = {"orders": []}
    # /trades has the matching execution.
    stub.responses[("GET", "/iserver/account/trades")] = [
        {
            "execution_id": "exec-x",
            "orderId": "id-rolled-off",
            "symbol": "AAPL",
            "side": "B",
            "size": 7,
            "price": 152.5,
            "trade_time_r": _epoch_ms(now),
        },
    ]
    snap = broker.get_order("id-rolled-off")
    assert snap.broker_order_id == "id-rolled-off"
    assert snap.status == "filled"
    assert snap.filled_quantity == Decimal("7")
    assert snap.avg_fill_price == Decimal("152.5")


def test_get_order_raises_when_not_found_anywhere(broker, stub):
    stub.responses[("GET", "/iserver/account/orders")] = {"orders": []}
    stub.responses[("GET", "/iserver/account/trades")] = []
    with pytest.raises(BrokerError, match="not found"):
        broker.get_order("nonexistent-id")


def test_status_mapping_derives_partial():
    """Direct test of the status-mapping helper — IBKR has no 'partial'
    status string, so partial is derived from filledQuantity > 0 < total."""
    assert _map_status("Submitted", Decimal("0"), Decimal("10")) == "submitted"
    assert _map_status("Submitted", Decimal("3"), Decimal("10")) == "partial"
    assert _map_status("Filled", Decimal("10"), Decimal("10")) == "filled"
    assert _map_status("Cancelled", Decimal("0"), Decimal("10")) == "cancelled"
    assert _map_status("PreSubmitted", Decimal("0"), Decimal("10")) == "submitted"
    # Unrecognised IBKR statuses map to 'error', NEVER silently to 'filled'.
    assert _map_status("WhoKnows", Decimal("0"), Decimal("10")) == "error"


# ---------------------------------------------------------------------------
# cancel_order
# ---------------------------------------------------------------------------


def test_cancel_order_calls_delete_endpoint(broker, stub):
    stub.responses[("DELETE", "/iserver/account/DU1234567/order/id-99")] = {
        "msg": "cancelled",
    }
    broker.cancel_order("id-99")
    assert (
        "DELETE",
        "/iserver/account/DU1234567/order/id-99",
        None, None,
    ) in stub.calls


# ---------------------------------------------------------------------------
# find_order_by_client_id (ADR 0008)
# ---------------------------------------------------------------------------


def test_find_order_by_client_id_matches_cOID(broker, stub):
    stub.responses[("GET", "/iserver/account/orders")] = {
        "orders": [
            {
                "orderId": "id-A",
                "cOID": "cid-A",
                "status": "Submitted",
                "ticker": "AAPL",
                "side": "BUY",
                "totalSize": 5,
                "filledQuantity": 0,
                "orderType": "LMT",
                "price": 100.0,
                "tif": "DAY",
            },
            {
                "orderId": "id-B",
                "cOID": "cid-B",
                "status": "Filled",
                "ticker": "MSFT",
                "side": "BUY",
                "totalSize": 2,
                "filledQuantity": 2,
                "orderType": "LMT",
                "price": 300.0,
                "tif": "DAY",
            },
        ],
    }
    found = broker.find_order_by_client_id("cid-B")
    assert found is not None
    assert found.broker_order_id == "id-B"
    assert found.status == "filled"


def test_find_order_by_client_id_returns_none_when_no_match(broker, stub):
    stub.responses[("GET", "/iserver/account/orders")] = {"orders": []}
    assert broker.find_order_by_client_id("not-here") is None


# ---------------------------------------------------------------------------
# conid resolution
# ---------------------------------------------------------------------------


def test_conid_resolution_picks_stk_section(broker, stub):
    """secdef/search can return multiple matches (STK, IND, etc). The
    adapter picks the first STK match."""
    stub.responses[("GET", "/iserver/secdef/search")] = [
        # An index result — the adapter should skip this.
        {
            "conid": 11111,
            "symbol": "AAPL",
            "sections": [{"secType": "IND"}],
        },
        # The actual stock.
        {
            "conid": 265598,
            "symbol": "AAPL",
            "sections": [{"secType": "STK"}],
        },
    ]
    stub.responses[("POST", "/iserver/account/DU1234567/orders")] = [
        {"order_id": "1", "order_status": "Submitted"},
    ]
    broker.submit_order(_ticket())
    submit_body = next(
        c for c in stub.calls
        if c[0] == "POST" and c[1].endswith("/orders")
    )[2]
    assert submit_body["orders"][0]["conid"] == 265598


def test_conid_resolution_uses_cache(broker, stub):
    """The conid lookup is cached per IBKRBroker instance — two orders
    on the same ticker should result in exactly one secdef/search call."""
    stub.responses[("GET", "/iserver/secdef/search")] = [
        {"conid": 265598, "symbol": "AAPL", "sections": [{"secType": "STK"}]},
    ]
    stub.responses[("POST", "/iserver/account/DU1234567/orders")] = [
        {"order_id": "1", "order_status": "Submitted"},
    ]
    broker.submit_order(_ticket(client_order_id="cid-1"))
    broker.submit_order(_ticket(client_order_id="cid-2"))
    secdef_calls = [
        c for c in stub.calls
        if c[0] == "GET" and c[1] == "/iserver/secdef/search"
    ]
    assert len(secdef_calls) == 1


def test_conid_resolution_raises_for_unknown_ticker(broker, stub):
    stub.responses[("GET", "/iserver/secdef/search")] = []
    with pytest.raises(BrokerError, match="could not resolve conid"):
        broker.submit_order(_ticket(ticker="XYZNEVERTRADED"))


# ---------------------------------------------------------------------------
# Risks #3 — cert-verify rule is enforced
# ---------------------------------------------------------------------------


def test_cert_verify_off_only_for_configured_url(settings):
    """ADR 0011 §1 + Risks #3: cert verification is disabled ONLY for the
    URL configured in settings.IBKR_GATEWAY_BASE_URL. Any other URL keeps
    verification on so a misconfigured / hostile host can't inherit the
    self-signed-cert trust."""
    from apps.brokers.adapters.ibkr_gateway import _verify_for
    settings.IBKR_GATEWAY_BASE_URL = "https://ibkr-gateway:5000"
    # The exact configured URL → verification OFF (returns False).
    assert _verify_for("https://ibkr-gateway:5000") is False
    assert _verify_for("https://ibkr-gateway:5000/") is False  # trailing slash
    # Anything else → verification ON.
    assert _verify_for("https://attacker.example:5000") is True
    assert _verify_for("https://ibkr-gateway:6000") is True  # different port
    assert _verify_for("http://ibkr-gateway:5000") is True   # http, not https


def test_gateway_session_uses_verify_rule(monkeypatch, settings):
    """The IBKRGatewaySession constructor passes the right `verify` flag
    to httpx.Client. Captured via a fake Client factory."""
    settings.IBKR_GATEWAY_BASE_URL = "https://ibkr-gateway:5000"
    captured: dict = {}

    class _FakeHttpxClient:
        def __init__(self, *, verify, timeout):
            captured["verify"] = verify
            captured["timeout"] = timeout
        def close(self): pass

    from apps.brokers.adapters import ibkr_gateway as gw_mod
    monkeypatch.setattr(gw_mod, "httpx", type("h", (), {
        "Client": _FakeHttpxClient,
        "RequestError": gw_mod.httpx.RequestError,
    }))

    # Default URL → verify=False (trusted gateway).
    gw_mod.IBKRGatewaySession()
    assert captured["verify"] is False

    # Explicit non-configured URL → verify=True.
    gw_mod.IBKRGatewaySession(base_url="https://something-else.example:5000")
    assert captured["verify"] is True
