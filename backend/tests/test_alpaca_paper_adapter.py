"""AlpacaPaperBroker adapter unit tests (P3a-4, ADR 0013).

Stub-client style — `_make_client` is monkey-patched per test to return a
`StubTradingClient` that records calls and returns canned SDK-shaped
objects. The HTTP boundary never runs. End-to-end cassettes against a
live Alpaca paper sandbox land during the manual checklist run.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model

from apps.brokers.adapters import alpaca_paper as alpaca_mod
from apps.brokers.adapters.alpaca_paper import (
    AlpacaPaperBroker,
    _is_duplicate_client_order_id,
    _map_status,
    _RateLimiter,
    assert_paper,
)
from apps.brokers.credentials import set_api_key_secret
from apps.brokers.interfaces import (
    BrokerError,
    OrderTicket,
)
from apps.brokers.models import BrokerAccount
from apps.portfolios.models import Portfolio

User = get_user_model()


# ---------------------------------------------------------------------------
# SDK stubs
# ---------------------------------------------------------------------------


@dataclass
class StubOrder:
    """Mimics the Alpaca SDK Order Pydantic model — only the fields the
    adapter reads."""

    id: str = "ord-abc"
    client_order_id: str = "coid-1"
    symbol: str = "AAPL"
    side: str = "buy"
    qty: str = "10"
    order_type: str = "market"
    limit_price: str | None = None
    time_in_force: str = "day"
    status: str = "new"
    filled_qty: str = "0"
    filled_avg_price: str | None = None
    filled_at: datetime | None = None
    updated_at: datetime | None = None

    def model_dump(self) -> dict:
        return {
            "id": self.id, "client_order_id": self.client_order_id,
            "symbol": self.symbol, "side": self.side, "qty": self.qty,
            "order_type": self.order_type, "limit_price": self.limit_price,
            "time_in_force": self.time_in_force, "status": self.status,
            "filled_qty": self.filled_qty,
            "filled_avg_price": self.filled_avg_price,
        }


@dataclass
class StubPosition:
    symbol: str = "AAPL"
    qty: str = "10"
    avg_entry_price: str = "150.00"
    side: str = "long"

    def model_dump(self) -> dict:
        return {
            "symbol": self.symbol, "qty": self.qty,
            "avg_entry_price": self.avg_entry_price, "side": self.side,
        }


@dataclass
class StubAccount:
    account_number: str = "PA-TEST-123"
    cash: str = "50000.00"
    buying_power: str = "100000.00"
    equity: str = "75000.00"
    currency: str = "USD"

    def model_dump(self) -> dict:
        return {
            "account_number": self.account_number, "cash": self.cash,
            "buying_power": self.buying_power, "equity": self.equity,
            "currency": self.currency,
        }


@dataclass
class StubTradingClient:
    """Stand-in for alpaca.trading.client.TradingClient. Records every
    call and returns whatever the test plants on the response slots."""

    account: StubAccount = field(default_factory=StubAccount)
    positions: list[StubPosition] = field(default_factory=list)
    orders: list[StubOrder] = field(default_factory=list)
    submit_response: Any = None
    submit_exception: Exception | None = None
    by_client_id: dict[str, StubOrder] = field(default_factory=dict)
    by_client_id_404: bool = False
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
        # SDK signature is positional `client_id` — match it. Test
        # bookkeeping still records as `client_order_id` so existing
        # assertions don't shift.
        self.calls.append(
            ("get_order_by_client_id", {"client_order_id": client_id}),
        )
        if self.by_client_id_404:
            import json as _json

            from alpaca.common.exceptions import APIError

            class _R:
                status_code = 404

            class _H:
                response = _R()
                request = None

            raise APIError(
                _json.dumps({"message": "order not found", "code": 40410000}),
                http_error=_H(),
            )
        return self.by_client_id.get(client_id)

    def cancel_order_by_id(self, order_id):
        self.calls.append(("cancel_order_by_id", {"order_id": order_id}))
        return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="alpaca-adapter@example.com", password="x" * 12,
    )


@pytest.fixture
def paper_account(user) -> BrokerAccount:
    p = Portfolio.objects.create(
        user=user, name="Broker · Alpaca paper",
        kind=Portfolio.KIND_BROKER, cash_balance=Decimal("0"),
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
        alpaca_mod, "_make_client",
        lambda api_key, api_secret: stub_client,
    )
    b = AlpacaPaperBroker(paper_account)
    b._test_stub = stub_client  # type: ignore[attr-defined]
    return b


# ---------------------------------------------------------------------------
# Capability + paper-only guards
# ---------------------------------------------------------------------------


def test_capabilities_ship_paper_only():
    cap = alpaca_mod.CAPABILITIES
    assert cap.code == "alpaca_paper"
    assert cap.supports_paper is True
    assert cap.supports_live is False
    assert cap.community_unverified is False
    assert cap.supports_fractional is True
    assert cap.auth_kind == "api_key"


def test_paper_only_no_live_host_in_module_source():
    """CI guard: the Alpaca live host string is forbidden from the
    adapter module. The SDK contains the live URL internally — that's
    fine; we just ensure no code path in *our* module reaches for it.
    Failing this test means someone added a live-host branch and broke
    the structural paper-only guarantee. ADR 0013 §1."""
    src = Path(alpaca_mod.__file__).read_text()
    assert "api.alpaca.markets" not in src, (
        "found live Alpaca host in adapter — paper-only guarantee broken"
    )
    # paper-api host is fine implicitly through SDK; verify we never
    # construct a non-paper client by checking we don't pass paper=False
    # or set url_override.
    assert "paper=False" not in src
    assert "url_override" not in src or "url_override is never set" in src


def test_assert_paper_rejects_live_mode(user):
    p = Portfolio.objects.create(
        user=user, name="x", kind=Portfolio.KIND_BROKER,
        cash_balance=Decimal("0"),
    )
    acc = BrokerAccount(
        user=user, broker="alpaca_paper",
        mode=BrokerAccount.MODE_LIVE, account_id="X",
        label="x", portfolio=p,
    )
    with pytest.raises(BrokerError, match="paper-only"):
        assert_paper(acc)


def test_account_creation_with_mode_live_is_rejected(db, user):
    """End-to-end: POST /api/broker-accounts/ with mode=live and Alpaca
    is rejected by the views validator because supports_live=False.
    This is the scaffolding-level paper-only guard. ADR 0013 §1."""
    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(user=user)
    r = client.post(
        "/api/broker-accounts/",
        {"broker": "alpaca_paper", "mode": "live", "label": "shouldfail"},
        format="json",
    )
    assert r.status_code == 400
    assert "no live mode" in str(r.data).lower() or "live" in str(r.data).lower()


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------


def test_map_status_known_and_unknown():
    assert _map_status("filled") == "filled"
    assert _map_status("partially_filled") == "partial"
    assert _map_status("new") == "submitted"
    assert _map_status("accepted") == "submitted"
    assert _map_status("canceled") == "cancelled"
    assert _map_status("expired") == "cancelled"
    assert _map_status("rejected") == "rejected"
    # Unknown → error, never silently filled.
    assert _map_status("something_new") == "error"
    assert _map_status(None) == "error"


# ---------------------------------------------------------------------------
# get_account
# ---------------------------------------------------------------------------


def test_get_account_maps_fields(broker, stub_client):
    stub_client.account = StubAccount(
        account_number="PA-9", cash="42.00", buying_power="84.00",
        equity="63.00", currency="USD",
    )
    snap = broker.get_account()
    assert snap.cash == Decimal("42.00")
    assert snap.buying_power == Decimal("84.00")
    assert snap.equity == Decimal("63.00")
    assert snap.currency == "USD"
    assert snap.account_id == "PA-9"


# ---------------------------------------------------------------------------
# get_positions — short comes out negative
# ---------------------------------------------------------------------------


def test_get_positions_signs_short_negative(broker, stub_client):
    stub_client.positions = [
        StubPosition(symbol="AAPL", qty="10", avg_entry_price="150", side="long"),
        StubPosition(symbol="TSLA", qty="5", avg_entry_price="200", side="short"),
    ]
    rows = broker.get_positions()
    aapl = next(r for r in rows if r.ticker == "AAPL")
    tsla = next(r for r in rows if r.ticker == "TSLA")
    assert aapl.quantity == Decimal("10")
    assert tsla.quantity == Decimal("-5")


def test_get_positions_skips_zero_quantity(broker, stub_client):
    stub_client.positions = [
        StubPosition(symbol="AAPL", qty="0", side="long"),
    ]
    assert broker.get_positions() == []


# ---------------------------------------------------------------------------
# submit_order
# ---------------------------------------------------------------------------


def test_submit_market_order_maps_to_dto(broker, stub_client):
    stub_client.submit_response = StubOrder(
        id="ord-1", client_order_id="my-coid", symbol="AAPL",
        side="buy", qty="3", order_type="market", status="accepted",
    )
    ticket = OrderTicket(
        client_order_id="my-coid", ticker="AAPL", side="buy",
        quantity=Decimal("3"), order_type="market",
    )
    snap = broker.submit_order(ticket)
    assert snap.broker_order_id == "ord-1"
    assert snap.client_order_id == "my-coid"
    assert snap.status == "submitted"
    assert snap.quantity == Decimal("3")
    assert snap.order_type == "market"
    # Verify the SDK request carried our client_order_id (native idempotency).
    last = [c for c in stub_client.calls if c[0] == "submit_order"][-1]
    assert getattr(last[1]["order_data"], "client_order_id", None) == "my-coid"


def test_submit_limit_order_includes_price(broker, stub_client):
    stub_client.submit_response = StubOrder(
        id="ord-2", client_order_id="coid-l", symbol="MSFT",
        side="sell", qty="0.5", order_type="limit", limit_price="500.00",
        status="new", time_in_force="gtc",
    )
    ticket = OrderTicket(
        client_order_id="coid-l", ticker="MSFT", side="sell",
        quantity=Decimal("0.5"), order_type="limit",
        limit_price=Decimal("500.00"), time_in_force="gtc",
    )
    snap = broker.submit_order(ticket)
    assert snap.limit_price == Decimal("500.00")
    assert snap.time_in_force == "gtc"
    # Fractional share carries through.
    assert snap.quantity == Decimal("0.5")


# ---------------------------------------------------------------------------
# Idempotency: duplicate client_order_id 422 resolves the existing order
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeHTTPError:
    def __init__(self, status_code: int) -> None:
        self.response = _FakeResponse(status_code)
        self.request = None


def _make_apierror(status: int, message: str, code: int = 42210000):
    import json as _json

    from alpaca.common.exceptions import APIError
    return APIError(
        _json.dumps({"code": code, "message": message}),
        http_error=_FakeHTTPError(status),
    )


def _make_duplicate_apierror():
    return _make_apierror(422, "client_order_id must be unique")


def test_is_duplicate_client_order_id_recogniser():
    assert _is_duplicate_client_order_id(_make_duplicate_apierror()) is True
    # Wrong message → not a duplicate.
    assert _is_duplicate_client_order_id(
        _make_apierror(422, "bad symbol", code=40010000),
    ) is False
    # Wrong status code → not a duplicate.
    assert _is_duplicate_client_order_id(
        _make_apierror(400, "client_order_id must be unique"),
    ) is False


def test_duplicate_submit_resolves_existing_order(broker, stub_client):
    """A retried POST with the same client_order_id gets HTTP 422 from
    Alpaca; the adapter resolves the pre-existing order via
    get_order_by_client_id and returns it instead of erroring or
    creating a second order. ADR 0013 §3."""
    stub_client.submit_exception = _make_duplicate_apierror()
    stub_client.by_client_id["coid-dupe"] = StubOrder(
        id="ord-original", client_order_id="coid-dupe", symbol="AAPL",
        side="buy", qty="2", status="accepted",
    )
    ticket = OrderTicket(
        client_order_id="coid-dupe", ticker="AAPL", side="buy",
        quantity=Decimal("2"), order_type="market",
    )
    snap = broker.submit_order(ticket)
    assert snap.broker_order_id == "ord-original"
    assert snap.client_order_id == "coid-dupe"
    # The lookup happened. Adapter passes the coid positionally as
    # `client_id` to match the SDK signature; the stub records it under
    # `client_order_id` for assertion convenience.
    lookups = [c for c in stub_client.calls if c[0] == "get_order_by_client_id"]
    assert len(lookups) == 1
    assert lookups[0][1]["client_order_id"] == "coid-dupe"


# ---------------------------------------------------------------------------
# find_order_by_client_id
# ---------------------------------------------------------------------------


def test_find_order_by_client_id_returns_snapshot(broker, stub_client):
    stub_client.by_client_id["coid-find"] = StubOrder(
        id="ord-9", client_order_id="coid-find", status="filled",
        filled_qty="10", filled_avg_price="151.50",
    )
    snap = broker.find_order_by_client_id("coid-find")
    assert snap is not None
    assert snap.broker_order_id == "ord-9"
    assert snap.status == "filled"
    assert snap.filled_quantity == Decimal("10")
    assert snap.avg_fill_price == Decimal("151.50")


def test_find_order_by_client_id_returns_none_on_404(broker, stub_client):
    stub_client.by_client_id_404 = True
    assert broker.find_order_by_client_id("coid-missing") is None


# ---------------------------------------------------------------------------
# cancel + get_order
# ---------------------------------------------------------------------------


def test_cancel_order_calls_sdk(broker, stub_client):
    broker.cancel_order("ord-cancel")
    assert ("cancel_order_by_id", {"order_id": "ord-cancel"}) in stub_client.calls


def test_get_order_returns_snapshot(broker, stub_client):
    stub_client.by_id["ord-get"] = StubOrder(
        id="ord-get", status="partially_filled",
        filled_qty="3", qty="5", filled_avg_price="100",
    )
    snap = broker.get_order("ord-get")
    assert snap.broker_order_id == "ord-get"
    assert snap.status == "partial"
    assert snap.filled_quantity == Decimal("3")


# ---------------------------------------------------------------------------
# get_recent_fills
# ---------------------------------------------------------------------------


def test_get_recent_fills_filters_and_maps(broker, stub_client):
    now = datetime.now(tz=UTC)
    stub_client.orders = [
        StubOrder(
            id="o1", symbol="AAPL", side="buy", filled_qty="2",
            filled_avg_price="150.00", filled_at=now - timedelta(minutes=5),
            status="filled",
        ),
        # Filled but before `since` — must be dropped.
        StubOrder(
            id="o2", symbol="TSLA", side="sell", filled_qty="1",
            filled_avg_price="200", filled_at=now - timedelta(hours=2),
            status="filled",
        ),
        # No fill yet — must be skipped.
        StubOrder(
            id="o3", symbol="MSFT", side="buy", filled_qty="0",
            filled_at=now, status="canceled",
        ),
    ]
    since = now - timedelta(minutes=30)
    fills = broker.get_recent_fills(since)
    assert len(fills) == 1
    fill = fills[0]
    assert fill.ticker == "AAPL"
    assert fill.quantity == Decimal("2")
    assert fill.price == Decimal("150.00")
    assert fill.side == "buy"


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


def test_rate_limiter_caps_calls_in_window():
    rl = _RateLimiter(max_calls=3, window_s=0.3)
    start = time.monotonic()
    for _ in range(3):
        rl.acquire()
    fast_elapsed = time.monotonic() - start
    # First 3 calls should fit comfortably under the window.
    assert fast_elapsed < 0.1
    # The 4th call must wait for the window to roll.
    rl.acquire()
    total = time.monotonic() - start
    assert total >= 0.25  # ≈ window_s minus the small slop


def test_rate_limiter_thread_safe():
    rl = _RateLimiter(max_calls=50, window_s=1.0)
    errors: list[Exception] = []

    def worker():
        try:
            for _ in range(5):
                rl.acquire()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
