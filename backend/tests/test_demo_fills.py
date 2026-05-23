"""Streamlined demo order flow + real-price fill engine (P3a-1 polish).

Demo (mock-broker) accounts skip the draft -> confirm gate: an order is
submitted straight to the demo book, market orders fill immediately, and
limit/stop orders rest until the live price crosses their trigger.

``demo_fills.live_price`` is monkeypatched so the tests are deterministic
and never touch the real quote provider.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.brokers import demo_fills
from apps.brokers.adapters import mock as mock_adapter
from apps.brokers.models import BrokerAccount, BrokerOrder
from apps.portfolios.models import Portfolio

User = get_user_model()


@pytest.fixture(autouse=True)
def _reset_mock_state():
    mock_adapter.reset_state()
    yield
    mock_adapter.reset_state()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="d@example.com", password="x" * 12)


@pytest.fixture
def auth_client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture
def demo_account(user) -> BrokerAccount:
    portfolio = Portfolio.objects.create(
        user=user, name="Broker · Demo", kind=Portfolio.KIND_BROKER,
        cash_balance=Decimal("100000"),
    )
    account = BrokerAccount.objects.create(
        user=user, broker="mock", mode=BrokerAccount.MODE_PAPER,
        account_id=f"demo-{user.id}", label="Demo book", portfolio=portfolio,
        connection_status=BrokerAccount.STATUS_ACTIVE,
    )
    mock_adapter.seed_demo_book(account, cash=Decimal("100000"))
    return account


def _fix_price(monkeypatch, price) -> None:
    monkeypatch.setattr(
        demo_fills, "live_price",
        lambda account, ticker: Decimal(str(price)),
    )


def _create(client, account, **kw):
    body = {
        "broker_account": account.id, "ticker": "AAPL", "side": "buy",
        "quantity": "5", "order_type": "market",
    }
    body.update(kw)
    return client.post("/api/broker/orders/", body, format="json")


# --- market orders ----------------------------------------------------------


def test_market_order_fills_immediately(auth_client, demo_account, monkeypatch):
    _fix_price(monkeypatch, "200")
    resp = _create(auth_client, demo_account, order_type="market", quantity="5")
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "filled"
    assert Decimal(body["avg_fill_price"]) == Decimal("200.0000")
    demo_account.portfolio.refresh_from_db()
    assert demo_account.portfolio.cash_balance == Decimal("99000.00")
    assert demo_account.portfolio.positions.filter(ticker="AAPL").count() == 1


# --- limit orders -----------------------------------------------------------


def test_buy_limit_below_market_rests(auth_client, demo_account, monkeypatch):
    # Market 200, buy limit 150 — not marketable, must rest as `submitted`.
    _fix_price(monkeypatch, "200")
    resp = _create(
        auth_client, demo_account, order_type="limit",
        side="buy", limit_price="150", quantity="2",
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "submitted"
    order = BrokerOrder.objects.get(pk=resp.json()["id"])
    assert order.fills.count() == 0


def test_marketable_buy_limit_fills_at_limit(auth_client, demo_account, monkeypatch):
    # Market 140 <= limit 150 — marketable, fills at the limit price.
    _fix_price(monkeypatch, "140")
    resp = _create(
        auth_client, demo_account, order_type="limit",
        side="buy", limit_price="150", quantity="2",
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "filled"
    assert Decimal(body["avg_fill_price"]) == Decimal("150.0000")


def test_resting_limit_fills_when_price_crosses(demo_account, monkeypatch):
    _fix_price(monkeypatch, "200")
    order = BrokerOrder.objects.create(
        broker_account=demo_account, ticker="AAPL", side="buy",
        quantity=Decimal("2"), order_type="limit", limit_price=Decimal("150"),
    )
    demo_fills.place_demo_order(order)
    order.refresh_from_db()
    assert order.status == BrokerOrder.STATUS_SUBMITTED

    # The market falls to 145 — the limit is now marketable.
    _fix_price(monkeypatch, "145")
    filled = demo_fills.evaluate_resting_demo_orders(demo_account)
    assert filled == 1
    order.refresh_from_db()
    assert order.status == BrokerOrder.STATUS_FILLED
    assert order.avg_fill_price == Decimal("150.0000")


# --- stop orders ------------------------------------------------------------


def test_resting_stop_triggers_at_market(demo_account, monkeypatch):
    # Sell stop at 90; market 100 is above the stop, so it must rest.
    _fix_price(monkeypatch, "100")
    order = BrokerOrder.objects.create(
        broker_account=demo_account, ticker="AAPL", side="sell",
        quantity=Decimal("3"), order_type="stop", stop_price=Decimal("90"),
    )
    demo_fills.place_demo_order(order)
    order.refresh_from_db()
    assert order.status == BrokerOrder.STATUS_SUBMITTED

    # The market drops through the stop -> the order fills at market.
    _fix_price(monkeypatch, "85")
    demo_fills.evaluate_resting_demo_orders(demo_account)
    order.refresh_from_db()
    assert order.status == BrokerOrder.STATUS_FILLED
    assert order.avg_fill_price == Decimal("85.0000")


# --- validation + cancel ----------------------------------------------------


def test_limit_order_requires_limit_price(auth_client, demo_account):
    resp = _create(auth_client, demo_account, order_type="limit", limit_price="")
    assert resp.status_code == 400
    assert "limit_price" in resp.json()


def test_stop_order_requires_stop_price(auth_client, demo_account):
    resp = _create(auth_client, demo_account, order_type="stop")
    assert resp.status_code == 400
    assert "stop_price" in resp.json()


def test_cancel_resting_demo_order(auth_client, demo_account, monkeypatch):
    _fix_price(monkeypatch, "200")
    resp = _create(
        auth_client, demo_account, order_type="limit",
        side="buy", limit_price="150",
    )
    order_id = resp.json()["id"]
    cancel = auth_client.post(
        f"/api/broker/orders/{order_id}/cancel/", {}, format="json",
    )
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"
