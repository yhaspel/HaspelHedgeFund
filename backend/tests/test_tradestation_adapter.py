"""TradeStationBroker adapter unit tests (P3a-3, ADR 0012).

Canned-response style — `_TSSession.request` is patched per test. The
HTTP boundary never runs. Cassettes recorded against a live SIM account
land during the manual integration run.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone

from apps.brokers.adapters import tradestation as ts_module
from apps.brokers.adapters.tradestation import (
    TradeStationBroker,
    _map_status,
    _trade_action,
    api_base_for_mode,
)
from apps.brokers.credentials import set_oauth_tokens
from apps.brokers.interfaces import (
    BrokerError,
    OrderMeta,
    OrderTicket,
)
from apps.brokers.models import BrokerAccount, BrokerCredential
from apps.portfolios.models import Portfolio

User = get_user_model()

SIM_BASE = "https://sim-api.tradestation.com/v3"
LIVE_BASE = "https://api.tradestation.com/v3"


class StubSession:
    """Replaces _TSSession instance on a TradeStationBroker. Records
    every call and returns canned values."""

    def __init__(self):
        self.responses: dict = {}
        self.calls: list = []

    def _resolve(self, method, path, *, params=None, json=None):
        self.calls.append((method, path, params, json))
        key = (method, path)
        if key in self.responses:
            v = self.responses[key]
        elif path in self.responses:
            v = self.responses[path]
        else:
            raise AssertionError(f"unhandled {method} {path}")
        if isinstance(v, Exception):
            raise v
        return v() if callable(v) else v

    def request(self, method, path, **kw):
        return self._resolve(method, path, **kw)

    def get(self, path, **kw):
        return self._resolve("GET", path, **kw)

    def post(self, path, **kw):
        return self._resolve("POST", path, **kw)

    def delete(self, path, **kw):
        return self._resolve("DELETE", path, **kw)


@pytest.fixture
def user(db):
    return User.objects.create_user(email="ts-adapter@example.com", password="x" * 12)


@pytest.fixture
def paper_account(user) -> BrokerAccount:
    p = Portfolio.objects.create(
        user=user, name="Broker · TS paper", kind=Portfolio.KIND_BROKER,
        cash_balance=Decimal("0"),
    )
    acc = BrokerAccount.objects.create(
        user=user, broker="tradestation", mode="paper",
        account_id="SIM12345", label="TS paper test", portfolio=p,
        connection_status=BrokerAccount.STATUS_ACTIVE,
        config={"api_base_url": SIM_BASE},
    )
    set_oauth_tokens(
        acc, access_token="live", refresh_token="r",
        expires_at=timezone.now() + timedelta(hours=1),
        flavor=BrokerCredential.AUTH_OAUTH2,
    )
    return acc


@pytest.fixture
def broker(paper_account, monkeypatch) -> TradeStationBroker:
    """Adapter with the HTTP session stubbed."""
    stub = StubSession()
    monkeypatch.setattr(
        ts_module, "_TSSession", lambda account: stub,
    )
    b = TradeStationBroker(paper_account)
    b._stub = stub  # type: ignore[attr-defined]  # test affordance
    return b


# ---------------------------------------------------------------------------
# Capability + helper unit tests
# ---------------------------------------------------------------------------


def test_capabilities_ship_live_disabled():
    assert ts_module.CAPABILITIES.code == "tradestation"
    assert ts_module.CAPABILITIES.supports_paper is True
    assert ts_module.CAPABILITIES.supports_live is False


def test_map_status_handles_known_and_unknown():
    assert _map_status("Filled") == "filled"
    assert _map_status("PartiallyFilled") == "partial"
    assert _map_status("Cancelled") == "cancelled"
    assert _map_status("Rejected") == "rejected"
    assert _map_status("Open") == "submitted"
    # Unknown → error, never silently filled.
    assert _map_status("SomethingNew") == "error"


@override_settings(
    TRADESTATION_API_BASE_SIM=SIM_BASE,
    TRADESTATION_API_BASE_LIVE=LIVE_BASE,
)
def test_api_base_routing_by_mode():
    assert api_base_for_mode("paper") == SIM_BASE
    assert api_base_for_mode("live") == LIVE_BASE


@override_settings(
    TRADESTATION_API_BASE_SIM=SIM_BASE,
    TRADESTATION_API_BASE_LIVE=LIVE_BASE,
)
def test_paper_account_with_live_url_is_refused(user):
    p = Portfolio.objects.create(
        user=user, name="X", kind=Portfolio.KIND_BROKER,
        cash_balance=Decimal("0"),
    )
    acc = BrokerAccount.objects.create(
        user=user, broker="tradestation", mode="paper",
        account_id="SIM1", label="x", portfolio=p,
        connection_status=BrokerAccount.STATUS_ACTIVE,
        config={"api_base_url": LIVE_BASE},
    )
    set_oauth_tokens(
        acc, access_token="t", refresh_token="r",
        expires_at=timezone.now() + timedelta(hours=1),
        flavor=BrokerCredential.AUTH_OAUTH2,
    )
    with pytest.raises(BrokerError, match="refusing"):
        TradeStationBroker(acc)


def test_trade_action_long_short_logic():
    assert _trade_action("buy", Decimal("0")) == "BUY"
    assert _trade_action("buy", Decimal("10")) == "BUY"
    assert _trade_action("buy", Decimal("-5")) == "BUYTOCOVER"
    assert _trade_action("sell", Decimal("10")) == "SELL"
    assert _trade_action("sell", Decimal("0")) == "SELLSHORT"
    assert _trade_action("sell", Decimal("-5")) == "SELLSHORT"


# ---------------------------------------------------------------------------
# get_account
# ---------------------------------------------------------------------------


def test_get_account_maps_balances(broker):
    broker._stub.responses[("GET", "/brokerage/accounts/SIM12345/balances")] = {
        "Balances": [{
            "CashBalance": "50000.00",
            "BuyingPower": "100000.00",
            "Equity": "75000.00",
            "Currency": "USD",
        }],
    }
    snap = broker.get_account()
    assert snap.cash == Decimal("50000.00")
    assert snap.buying_power == Decimal("100000.00")
    assert snap.equity == Decimal("75000.00")
    assert snap.currency == "USD"


# ---------------------------------------------------------------------------
# get_positions — short comes out negative
# ---------------------------------------------------------------------------


def test_get_positions_signs_short_negative(broker):
    broker._stub.responses[("GET", "/brokerage/accounts/SIM12345/positions")] = {
        "Positions": [
            {"Symbol": "AAPL", "Quantity": "10", "AveragePrice": "150",
             "LongShort": "Long"},
            {"Symbol": "MSFT", "Quantity": "5", "AveragePrice": "300",
             "LongShort": "Short"},
            {"Symbol": "GOOG", "Quantity": "0", "AveragePrice": "0",
             "LongShort": "Long"},  # zero-qty filtered
        ],
    }
    pos = broker.get_positions()
    by_t = {p.ticker: p for p in pos}
    assert by_t["AAPL"].quantity == Decimal("10")
    assert by_t["MSFT"].quantity == Decimal("-5")
    assert "GOOG" not in by_t


# ---------------------------------------------------------------------------
# submit_order — clean path
# ---------------------------------------------------------------------------


def _ticket(**kw):
    base = dict(
        client_order_id="cid-1", ticker="AAPL", side="buy",
        quantity=Decimal("10"), order_type="limit",
        limit_price=Decimal("150"), time_in_force="day",
    )
    base.update(kw)
    return OrderTicket(**base)


def test_submit_order_clean_path(broker):
    # submit_order calls get_positions first to derive TradeAction.
    broker._stub.responses[("GET", "/brokerage/accounts/SIM12345/positions")] = {
        "Positions": [],
    }
    broker._stub.responses[("POST", "/orderexecution/orders")] = {
        "Orders": [{"OrderID": "ORD-99", "Message": "Order received"}],
        "Errors": [],
    }
    snap = broker.submit_order(_ticket())
    assert snap.broker_order_id == "ORD-99"
    assert snap.status == "submitted"
    assert snap.client_order_id == "cid-1"
    # Body carries the right TradeAction (flat → BUY for buy).
    posted = next(
        c for c in broker._stub.calls
        if c[0] == "POST" and c[1] == "/orderexecution/orders"
    )
    body = posted[3]
    assert body["TradeAction"] == "BUY"
    assert body["OrderType"] == "Limit"
    assert body["LimitPrice"] == "150"
    assert body["Symbol"] == "AAPL"


def test_submit_order_short_entry_picks_sellshort(broker):
    broker._stub.responses[("GET", "/brokerage/accounts/SIM12345/positions")] = {
        "Positions": [],
    }
    broker._stub.responses[("POST", "/orderexecution/orders")] = {
        "Orders": [{"OrderID": "ORD-100"}], "Errors": [],
    }
    broker.submit_order(_ticket(side="sell"))  # flat → short entry
    posted = next(
        c for c in broker._stub.calls
        if c[0] == "POST" and c[1] == "/orderexecution/orders"
    )
    assert posted[3]["TradeAction"] == "SELLSHORT"


def test_submit_order_long_exit_picks_sell(broker):
    broker._stub.responses[("GET", "/brokerage/accounts/SIM12345/positions")] = {
        "Positions": [
            {"Symbol": "AAPL", "Quantity": "20", "AveragePrice": "100",
             "LongShort": "Long"},
        ],
    }
    broker._stub.responses[("POST", "/orderexecution/orders")] = {
        "Orders": [{"OrderID": "ORD-101"}], "Errors": [],
    }
    broker.submit_order(_ticket(side="sell"))
    posted = next(
        c for c in broker._stub.calls
        if c[0] == "POST" and c[1] == "/orderexecution/orders"
    )
    assert posted[3]["TradeAction"] == "SELL"


def test_submit_order_short_cover_picks_buytocover(broker):
    broker._stub.responses[("GET", "/brokerage/accounts/SIM12345/positions")] = {
        "Positions": [
            {"Symbol": "AAPL", "Quantity": "5", "AveragePrice": "100",
             "LongShort": "Short"},
        ],
    }
    broker._stub.responses[("POST", "/orderexecution/orders")] = {
        "Orders": [{"OrderID": "ORD-102"}], "Errors": [],
    }
    broker.submit_order(_ticket(side="buy"))
    posted = next(
        c for c in broker._stub.calls
        if c[0] == "POST" and c[1] == "/orderexecution/orders"
    )
    assert posted[3]["TradeAction"] == "BUYTOCOVER"


def test_submit_order_errors_block_raise(broker):
    broker._stub.responses[("GET", "/brokerage/accounts/SIM12345/positions")] = {
        "Positions": [],
    }
    broker._stub.responses[("POST", "/orderexecution/orders")] = {
        "Orders": [], "Errors": [{"OrderID": "?", "Error": "BadSymbol"}],
    }
    with pytest.raises(BrokerError, match="BadSymbol"):
        broker.submit_order(_ticket())


# ---------------------------------------------------------------------------
# get_order / cancel_order
# ---------------------------------------------------------------------------


def test_get_order_returns_snapshot(broker):
    broker._stub.responses[("GET", "/brokerage/accounts/SIM12345/orders/O1")] = {
        "Orders": [{
            "OrderID": "O1", "Status": "Filled",
            "Legs": [{"Symbol": "AAPL", "BuyOrSell": "Buy",
                      "QuantityOrdered": "10", "ExecQuantity": "10",
                      "ExecutionPrice": "151.00"}],
            "OrderType": "Limit", "LimitPrice": "150",
            "TimeInForce": {"Duration": "DAY"},
        }],
    }
    snap = broker.get_order("O1")
    assert snap.broker_order_id == "O1"
    assert snap.status == "filled"
    assert snap.filled_quantity == Decimal("10")
    assert snap.avg_fill_price == Decimal("151.00")


def test_cancel_order_calls_delete(broker):
    broker._stub.responses[("DELETE", "/orderexecution/orders/O7")] = None
    broker.cancel_order("O7")
    assert any(
        c[0] == "DELETE" and c[1] == "/orderexecution/orders/O7"
        for c in broker._stub.calls
    )


# ---------------------------------------------------------------------------
# find_order_by_client_id heuristic scan
# ---------------------------------------------------------------------------


def test_find_order_by_client_id_without_meta_returns_none(broker):
    assert broker.find_order_by_client_id("cid-x") is None


def test_find_order_heuristic_match(broker):
    created = datetime.now(tz=UTC)
    broker._stub.responses[("GET", "/brokerage/accounts/SIM12345/orders")] = {
        "Orders": [
            {
                "OrderID": "MATCH",
                "Status": "Open",
                "OpenedDateTime": created.isoformat().replace("+00:00", "Z"),
                "Legs": [{"Symbol": "AAPL", "BuyOrSell": "Buy",
                          "QuantityOrdered": "10"}],
                "OrderType": "Limit", "LimitPrice": "150",
            },
            {
                "OrderID": "WRONGSYMBOL",
                "Status": "Open",
                "OpenedDateTime": created.isoformat().replace("+00:00", "Z"),
                "Legs": [{"Symbol": "MSFT", "BuyOrSell": "Buy",
                          "QuantityOrdered": "10"}],
                "OrderType": "Limit",
            },
        ],
    }
    meta = OrderMeta(
        ticker="AAPL", side="buy", quantity=Decimal("10"), created_at=created,
    )
    snap = broker.find_order_by_client_id("cid-zz", order_meta=meta)
    assert snap is not None
    assert snap.broker_order_id == "MATCH"
    assert snap.client_order_id == "cid-zz"


def test_find_order_heuristic_no_match_returns_none(broker):
    created = datetime.now(tz=UTC)
    broker._stub.responses[("GET", "/brokerage/accounts/SIM12345/orders")] = {
        "Orders": [
            {
                "OrderID": "WRONGQTY",
                "Status": "Open",
                "OpenedDateTime": created.isoformat().replace("+00:00", "Z"),
                "Legs": [{"Symbol": "AAPL", "BuyOrSell": "Buy",
                          "QuantityOrdered": "5"}],
                "OrderType": "Limit",
            },
        ],
    }
    meta = OrderMeta(
        ticker="AAPL", side="buy", quantity=Decimal("10"), created_at=created,
    )
    assert broker.find_order_by_client_id("cid-zz", order_meta=meta) is None
