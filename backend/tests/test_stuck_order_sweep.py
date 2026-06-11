"""P10 §A2 — the order-domain-403 → rejection classification (so an
insufficient-qty order terminates instead of lingering as `confirmed`) and the
sweep that reaps already-stuck `confirmed` orders.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.brokers.interfaces import BrokerAuthError, BrokerError
from apps.brokers.models import BrokerAccount, BrokerOrder
from apps.brokers.tasks import sweep_stuck_confirmed_orders


class _FakeAPIError(Exception):
    def __init__(self, status_code: int, body: str):
        super().__init__(body)
        self.status_code = status_code


def _translate(exc):
    # Patch the SDK APIError symbol the translator imports to our fake, then run it.
    import alpaca.common.exceptions as ae

    import apps.brokers.adapters.alpaca_paper as ap
    orig = ae.APIError
    ae.APIError = _FakeAPIError  # type: ignore[assignment]
    try:
        ap._raise_translated(exc, "submit_order")
    finally:
        ae.APIError = orig


def test_insufficient_qty_403_is_a_rejection_not_auth() -> None:
    exc = _FakeAPIError(403, '{"available":"19","code":40310000,"existing_qty":"22"}')
    with pytest.raises(BrokerError):
        _translate(exc)


def test_insufficient_qty_403_with_string_code_is_a_rejection() -> None:
    # Defensive: Alpaca code arriving as a string must still classify as reject.
    exc = _FakeAPIError(403, '{"available":"19","code":"40310000"}')
    with pytest.raises(BrokerError):
        _translate(exc)


def test_genuine_auth_403_still_raises_auth() -> None:
    exc = _FakeAPIError(403, '{"code":40110000,"message":"forbidden"}')
    with pytest.raises(BrokerAuthError):
        _translate(exc)


def test_401_still_raises_auth() -> None:
    exc = _FakeAPIError(401, '{"message":"unauthorized"}')
    with pytest.raises(BrokerAuthError):
        _translate(exc)


@pytest.mark.django_db
def test_sweep_reaps_old_stuck_confirmed_with_error() -> None:
    from apps.portfolios.models import Portfolio

    uid = _user()
    pf = Portfolio.objects.create(user_id=uid, name="rp book", kind=Portfolio.KIND_BROKER)
    acc = BrokerAccount.objects.create(
        user_id=uid, broker="alpaca_paper", label="rp", mode="paper",
        account_id="x", connection_status=BrokerAccount.STATUS_ACTIVE, portfolio=pf,
    )
    old = BrokerOrder.objects.create(
        broker_account=acc, ticker="MRVL", side="sell", quantity=Decimal("22"),
        order_type="market", status=BrokerOrder.STATUS_CONFIRMED,
        broker_order_id="", error_message="Alpaca submit_order → 403: insufficient",
    )
    BrokerOrder.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - timedelta(hours=48)
    )
    # A recent confirmed+error order is within the grace window → left alone.
    recent = BrokerOrder.objects.create(
        broker_account=acc, ticker="MRVL", side="sell", quantity=Decimal("22"),
        order_type="market", status=BrokerOrder.STATUS_CONFIRMED,
        broker_order_id="", error_message="transient auth blip",
    )
    # A confirmed order with NO error (legitimately awaiting submission) → left alone.
    clean = BrokerOrder.objects.create(
        broker_account=acc, ticker="SPY", side="buy", quantity=Decimal("1"),
        order_type="market", status=BrokerOrder.STATUS_CONFIRMED, broker_order_id="",
    )
    BrokerOrder.objects.filter(pk=clean.pk).update(
        created_at=timezone.now() - timedelta(hours=48)
    )

    reaped = sweep_stuck_confirmed_orders(grace_hours=24)

    assert reaped == 1
    old.refresh_from_db()
    recent.refresh_from_db()
    clean.refresh_from_db()
    assert old.status == BrokerOrder.STATUS_REJECTED
    assert "swept" in old.error_message
    assert recent.status == BrokerOrder.STATUS_CONFIRMED  # too recent
    assert clean.status == BrokerOrder.STATUS_CONFIRMED   # no error → not dead


def _user() -> int:
    from django.contrib.auth import get_user_model

    u, _ = get_user_model().objects.get_or_create(email="sweep@test.local")
    return u.id
