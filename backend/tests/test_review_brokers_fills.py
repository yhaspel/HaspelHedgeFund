"""Adversarial review (reviewer: brokers) — fill bookkeeping proofs.

`reconcile._apply_fill_to_book` keeps the OLD leg's avg_cost when a single
fill flips a position through zero (long 5 → sell 8 → short 3). The new short
leg's cost basis must be the fill price; instead it inherits the long's cost.
Reachable via (a) the demo book (no venue clamp) and (b) a fund SLEEVE book:
`venue_fit` only clamps to the ACCOUNT's net position, so when a sibling sleeve
holds the same name the sleeve's own book can cross zero in one fill.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from apps.brokers.adapters import mock as mock_adapter
from apps.brokers.models import BrokerAccount, BrokerFill, BrokerOrder
from apps.brokers.reconcile import _apply_fill_to_book
from apps.portfolios.models import LedgerEntry, Portfolio, Position

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="rv-fills@example.com", password="x" * 12)


@pytest.fixture
def account(user):
    pf = Portfolio.objects.create(
        user=user, name="bk", kind=Portfolio.KIND_BROKER, cash_balance=Decimal("100000"),
    )
    acc = BrokerAccount.objects.create(
        user=user, broker="mock", mode="paper", account_id="d", label="d", portfolio=pf,
        connection_status=BrokerAccount.STATUS_ACTIVE,
    )
    mock_adapter.seed_demo_book(acc, cash=Decimal("100000"))
    return acc


def _fill(order, qty, price):
    return BrokerFill.objects.create(
        order=order, broker_fill_id=f"f-{order.pk}-{qty}", quantity=Decimal(qty),
        price=Decimal(price), filled_at=datetime.now(tz=UTC),
    )


def test_flip_through_zero_sets_new_leg_cost_basis_to_fill_price(account):
    """FIXED (B3c): on a flip through zero the closed leg realizes P&L and the
    remainder is a NEW leg opened at the fill price."""
    pf = account.portfolio
    Position.objects.create(
        portfolio=pf, ticker="AAPL", quantity=Decimal("5"), avg_cost=Decimal("100"),
    )
    order = BrokerOrder.objects.create(
        broker_account=account, ticker="AAPL", side="sell", quantity=Decimal("8"),
    )
    _apply_fill_to_book(pf, order, _fill(order, "8", "120"))
    pos = Position.objects.get(portfolio=pf, ticker="AAPL")
    assert pos.quantity == Decimal("-3")
    assert pos.realized_pnl == Decimal("100.00")        # (120-100) × 5
    assert pos.avg_cost == Decimal("120")               # the short was opened at 120


def test_flip_through_zero_keeps_stale_cost_basis(account):
    """FIXED (B3c): same scenario, following the flipped leg to its cover.

    Was: the short inherited the long's $100 basis, so covering the 3 at the
    very price it was opened at booked a phantom -$60 loss."""
    pf = account.portfolio
    Position.objects.create(
        portfolio=pf, ticker="AAPL", quantity=Decimal("5"), avg_cost=Decimal("100"),
    )
    order = BrokerOrder.objects.create(
        broker_account=account, ticker="AAPL", side="sell", quantity=Decimal("8"),
    )
    _apply_fill_to_book(pf, order, _fill(order, "8", "120"))
    pos = Position.objects.get(portfolio=pf, ticker="AAPL")
    assert pos.quantity == Decimal("-3")
    assert pos.avg_cost == Decimal("120")   # the short really was opened at 120
    # Covering the 3 at 120 is a wash — no phantom loss.
    cover = BrokerOrder.objects.create(
        broker_account=account, ticker="AAPL", side="buy", quantity=Decimal("3"),
    )
    _apply_fill_to_book(pf, cover, _fill(cover, "3", "120"))
    entry = LedgerEntry.objects.filter(portfolio=pf, broker_order=cover).get()
    assert entry.realized_pnl == Decimal("0.00")
    assert not Position.objects.filter(portfolio=pf, ticker="AAPL").exists()


def test_flip_through_zero_the_other_way_short_to_long(account):
    """The mirror case: short 4 @ 50 → buy 10 @ 40 → long 6 based at 40."""
    pf = account.portfolio
    Position.objects.create(
        portfolio=pf, ticker="TLT", quantity=Decimal("-4"), avg_cost=Decimal("50"),
    )
    order = BrokerOrder.objects.create(
        broker_account=account, ticker="TLT", side="buy", quantity=Decimal("10"),
    )
    _apply_fill_to_book(pf, order, _fill(order, "10", "40"))
    pos = Position.objects.get(portfolio=pf, ticker="TLT")
    assert pos.quantity == Decimal("6")
    assert pos.realized_pnl == Decimal("40.00")   # (50-40) × 4
    assert pos.avg_cost == Decimal("40")


def test_exact_close_to_zero_deletes_the_position(account):
    """Control: closing exactly flat must not resurrect a leg at the fill price."""
    pf = account.portfolio
    Position.objects.create(
        portfolio=pf, ticker="IWM", quantity=Decimal("5"), avg_cost=Decimal("100"),
    )
    order = BrokerOrder.objects.create(
        broker_account=account, ticker="IWM", side="sell", quantity=Decimal("5"),
    )
    _apply_fill_to_book(pf, order, _fill(order, "5", "110"))
    assert not Position.objects.filter(portfolio=pf, ticker="IWM").exists()
    entry = LedgerEntry.objects.filter(portfolio=pf, broker_order=order).get()
    assert entry.realized_pnl == Decimal("50.00")


def test_reduce_without_flip_is_correct(account):
    """Control: a plain reduction realizes correctly and keeps the basis."""
    pf = account.portfolio
    Position.objects.create(
        portfolio=pf, ticker="AAPL", quantity=Decimal("5"), avg_cost=Decimal("100"),
    )
    order = BrokerOrder.objects.create(
        broker_account=account, ticker="AAPL", side="sell", quantity=Decimal("2"),
    )
    _apply_fill_to_book(pf, order, _fill(order, "2", "120"))
    pos = Position.objects.get(portfolio=pf, ticker="AAPL")
    assert (pos.quantity, pos.avg_cost, pos.realized_pnl) == (
        Decimal("3"), Decimal("100"), Decimal("40.00"),
    )
