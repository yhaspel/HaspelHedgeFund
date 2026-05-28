"""DELETE /api/broker-accounts/<id>/ tests."""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.brokers.models import BrokerAccount, BrokerOrder
from apps.portfolios.models import Portfolio

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="del@example.com", password="x" * 12)


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _mk(user, *, status=BrokerAccount.STATUS_CONNECTING):
    p = Portfolio.objects.create(
        user=user, name="x", kind=Portfolio.KIND_BROKER,
        cash_balance=Decimal("0"),
    )
    return BrokerAccount.objects.create(
        user=user, broker="tradestation", mode="paper",
        account_id="pending-1", label="x", portfolio=p,
        connection_status=status,
    )


def test_delete_connecting_account_succeeds(user, client):
    acc = _mk(user)
    r = client.delete(f"/api/broker-accounts/{acc.pk}/")
    assert r.status_code == 204
    assert not BrokerAccount.objects.filter(pk=acc.pk).exists()
    assert not Portfolio.objects.filter(pk=acc.portfolio_id).exists()


def test_delete_refuses_when_orders_in_flight(user, client):
    acc = _mk(user, status=BrokerAccount.STATUS_ACTIVE)
    BrokerOrder.objects.create(
        broker_account=acc, ticker="AAPL", side="buy",
        quantity=Decimal("1"), order_type="market",
        idempotency_state=BrokerOrder.IDEM_SUBMIT_PENDING,
    )
    r = client.delete(f"/api/broker-accounts/{acc.pk}/")
    assert r.status_code == 400
    assert "in-flight" in str(r.content)
    assert BrokerAccount.objects.filter(pk=acc.pk).exists()


def test_delete_other_users_account_is_404(client, db):
    other = User.objects.create_user(email="o@example.com", password="x" * 12)
    acc = _mk(other)
    r = client.delete(f"/api/broker-accounts/{acc.pk}/")
    assert r.status_code == 400  # _get raises 400 with detail=not found
    assert BrokerAccount.objects.filter(pk=acc.pk).exists()
