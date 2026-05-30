"""P4 WS-E isolation guarantee: strategy enrollment never touches the Manual
Book, even when the same ticker is held in both books.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from apps.portfolios import manual_book, runs_bridge
from apps.portfolios.models import (
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    Position,
    RebalanceOrder,
    Universe,
)

User = get_user_model()


@pytest.fixture(autouse=True)
def _no_live_marks():
    with patch("apps.portfolios.valuation.get_mark", return_value=None):
        yield


@pytest.fixture
def user(db):
    return User.objects.create_user(email="ei@example.com", password="x")


def _strategy_target(user, weights, prices):
    universe = Universe.objects.create(name=f"ei-{user.id}", is_active=True)
    portfolio = Portfolio.objects.create(
        user=user, name="strat book", kind="strategy", cash_balance=Decimal("100000"),
    )
    strategy = PortfolioStrategy.objects.create(
        user=user, name="s", kind="long_only", universe=universe, portfolio=portfolio,
    )
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date(2026, 5, 29), status="done",
        target_weights=weights,
    )
    for i, (t, px) in enumerate(prices.items()):
        RebalanceOrder.objects.create(
            target=target, ticker=t, side="buy", quantity=Decimal("1"),
            limit_price=Decimal(str(px)), reason="open",
            estimated_notional_usd=Decimal("0"), sequence=i,
        )
    return strategy, portfolio, target


@pytest.mark.django_db
def test_manual_book_same_ticker_untouched(user) -> None:
    # Manual Book holds NVDA.
    manual_book.open_or_increase_position(
        user=user, ticker="NVDA", side="long",
        quantity=Decimal("5"), entry_price=Decimal("100"),
    )
    manual = Portfolio.objects.get(user=user, kind=Portfolio.KIND_MANUAL)
    manual_nvda_before = Position.objects.get(portfolio=manual, ticker="NVDA")
    cash_before = manual.cash_balance

    strategy, strat_book, target = _strategy_target(user, {"NVDA": 0.03}, {"NVDA": 100})
    runs_bridge.enroll_target_into_portfolio(target, mode="auto")

    # Manual NVDA position + cash are byte-identical after enrollment.
    manual.refresh_from_db()
    manual_nvda_after = Position.objects.get(portfolio=manual, ticker="NVDA")
    assert manual_nvda_after.quantity == manual_nvda_before.quantity == Decimal("5")
    assert manual_nvda_after.avg_cost == manual_nvda_before.avg_cost
    assert manual.cash_balance == cash_before
    # The strategy book opened its OWN NVDA position, separate from the manual one.
    strat_nvda = Position.objects.get(portfolio=strat_book, ticker="NVDA")
    assert strat_nvda.id != manual_nvda_after.id
    assert strat_nvda.quantity == Decimal("30")


@pytest.mark.django_db
def test_strategy_positions_absent_from_manual_book_query(user) -> None:
    strategy, strat_book, target = _strategy_target(user, {"AAPL": 0.03}, {"AAPL": 100})
    runs_bridge.enroll_target_into_portfolio(target, mode="auto")
    manual = manual_book.get_or_create_manual_book(user)
    manual_tickers = set(
        Position.objects.filter(portfolio=manual).values_list("ticker", flat=True)
    )
    assert "AAPL" not in manual_tickers
    assert Position.objects.filter(portfolio=manual).count() == 0


@pytest.mark.django_db
def test_enroll_into_manual_book_no_side_effects(user) -> None:
    strategy, strat_book, target = _strategy_target(user, {"AAPL": 0.03}, {"AAPL": 100})
    # Repoint the strategy at a manual-kind portfolio (defensive path).
    Portfolio.objects.filter(pk=strat_book.pk).update(kind=Portfolio.KIND_MANUAL)
    target.strategy.portfolio.refresh_from_db()
    with pytest.raises(runs_bridge.EnrollmentError):
        runs_bridge.enroll_target_into_portfolio(target, mode="auto")
    assert Position.objects.filter(portfolio_id=strat_book.pk).count() == 0
    target.refresh_from_db()
    assert target.enrolled_at is None
