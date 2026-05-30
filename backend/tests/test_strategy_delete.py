"""P4 WS-C: delete an unrun strategy.

Gated on (a) no non-cancelled cycles and (b) an empty strategy-portfolio book.
A freshly-seeded zero-position / zero-ledger book is detached + deleted with
the strategy; a book with positions or ledger history is refused with 409.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from apps.portfolios.models import (
    LedgerEntry,
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    Position,
    Universe,
)

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="sd@example.com", password="supersecret")


def _client(email: str) -> APIClient:
    c = APIClient()
    token = c.post(
        reverse("login"), {"email": email, "password": "supersecret"}, format="json"
    ).data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return c


def _strategy(user, name="s") -> PortfolioStrategy:
    universe = Universe.objects.create(name=f"sd-uni-{name}", is_active=True)
    portfolio = Portfolio.objects.create(
        user=user, name=f"book-{name}", kind="strategy", cash_balance=Decimal("100000"),
    )
    return PortfolioStrategy.objects.create(
        user=user, name=name, universe=universe, portfolio=portfolio,
    )


@pytest.mark.django_db
def test_delete_strategy_no_targets_succeeds_and_removes_book(user) -> None:
    strategy = _strategy(user)
    portfolio_id = strategy.portfolio_id
    client = _client("sd@example.com")
    resp = client.delete(reverse("strategy-detail", args=[strategy.pk]))
    assert resp.status_code == 204
    assert not PortfolioStrategy.objects.filter(pk=strategy.pk).exists()
    # freshly-seeded empty book is cleaned up too
    assert not Portfolio.objects.filter(pk=portfolio_id).exists()


@pytest.mark.django_db
def test_delete_strategy_with_only_cancelled_target_succeeds(user) -> None:
    strategy = _strategy(user)
    PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date(2026, 5, 1), status="cancelled",
    )
    client = _client("sd@example.com")
    resp = client.delete(reverse("strategy-detail", args=[strategy.pk]))
    assert resp.status_code == 204
    assert not PortfolioStrategy.objects.filter(pk=strategy.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("status_val", ["done", "failed", "awaiting_review", "running"])
def test_delete_strategy_with_noncancelled_target_409s(user, status_val: str) -> None:
    strategy = _strategy(user, name=f"s-{status_val}")
    PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date(2026, 5, 1), status=status_val,
    )
    client = _client("sd@example.com")
    resp = client.delete(reverse("strategy-detail", args=[strategy.pk]))
    assert resp.status_code == 409
    assert PortfolioStrategy.objects.filter(pk=strategy.pk).exists()


@pytest.mark.django_db
def test_delete_strategy_with_position_in_book_409s(user) -> None:
    strategy = _strategy(user)
    Position.objects.create(
        portfolio=strategy.portfolio, ticker="AAPL",
        quantity=Decimal("10"), avg_cost=Decimal("100"),
    )
    client = _client("sd@example.com")
    resp = client.delete(reverse("strategy-detail", args=[strategy.pk]))
    assert resp.status_code == 409
    assert PortfolioStrategy.objects.filter(pk=strategy.pk).exists()
    assert Portfolio.objects.filter(pk=strategy.portfolio_id).exists()


@pytest.mark.django_db
def test_delete_strategy_with_ledger_history_409s(user) -> None:
    strategy = _strategy(user)
    LedgerEntry.objects.create(
        portfolio=strategy.portfolio, kind=LedgerEntry.KIND_DEPOSIT,
        cash_delta=Decimal("100000"), cash_balance_after=Decimal("100000"),
    )
    client = _client("sd@example.com")
    resp = client.delete(reverse("strategy-detail", args=[strategy.pk]))
    assert resp.status_code == 409
    assert PortfolioStrategy.objects.filter(pk=strategy.pk).exists()


@pytest.mark.django_db
def test_delete_other_users_strategy_404s(user) -> None:
    strategy = _strategy(user)
    User.objects.create_user(email="intruder@example.com", password="supersecret")
    client = _client("intruder@example.com")
    resp = client.delete(reverse("strategy-detail", args=[strategy.pk]))
    assert resp.status_code == 404
    assert PortfolioStrategy.objects.filter(pk=strategy.pk).exists()


@pytest.mark.django_db
def test_targets_count_active_reflects_noncancelled_cycles(user) -> None:
    strategy = _strategy(user)
    PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date(2026, 5, 1), status="cancelled")
    PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date(2026, 5, 2), status="done")
    PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date(2026, 5, 3), status="failed")
    client = _client("sd@example.com")
    # list view (annotated)
    listing = client.get(reverse("strategies"))
    row = next(r for r in listing.data if r["id"] == strategy.pk)
    assert row["targets_count_active"] == 2  # done + failed, cancelled excluded
    # detail view (annotated)
    detail = client.get(reverse("strategy-detail", args=[strategy.pk]))
    assert detail.data["targets_count_active"] == 2
