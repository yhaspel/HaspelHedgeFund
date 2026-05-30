"""P4 WS-E: enrollment apply (auto + manual), delta actions, and the
auto-enroll-on-done hook. Marks forced to the limit_price fallback for
deterministic sizing.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from apps.portfolios import runs_bridge
from apps.portfolios.models import (
    LedgerEntry,
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
    return User.objects.create_user(email="ea@example.com", password="supersecret")


def _client(email: str) -> APIClient:
    c = APIClient()
    token = c.post(
        reverse("login"), {"email": email, "password": "supersecret"}, format="json"
    ).data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return c


def setup_target(user, weights, prices, *, kind="long_short", cash=Decimal("100000")):
    universe = Universe.objects.create(name=f"ea-{user.id}-{id(weights)}", is_active=True)
    portfolio = Portfolio.objects.create(
        user=user, name="book", kind="strategy", cash_balance=cash,
    )
    strategy = PortfolioStrategy.objects.create(
        user=user, name="s", kind=kind, universe=universe, portfolio=portfolio,
    )
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date(2026, 5, 29), status="done",
        target_weights=weights,
    )
    for i, (ticker, px) in enumerate(prices.items()):
        RebalanceOrder.objects.create(
            target=target, ticker=ticker,
            side="buy" if weights.get(ticker, 0) >= 0 else "short",
            quantity=Decimal("1"), limit_price=Decimal(str(px)),
            reason="open", estimated_notional_usd=Decimal("0"), sequence=i,
        )
    return strategy, portfolio, target


@pytest.mark.django_db
def test_auto_mode_materializes_all(user) -> None:
    strategy, portfolio, target = setup_target(
        user, {"NVDA": 0.03, "AAPL": -0.03}, {"NVDA": 100, "AAPL": 100},
    )
    client = _client("ea@example.com")
    resp = client.post(
        reverse("strategy-enroll", args=[strategy.pk, target.pk]),
        {"mode": "auto"}, format="json",
    )
    assert resp.status_code == 201
    positions = {p.ticker: p for p in Position.objects.filter(portfolio=portfolio)}
    assert positions["NVDA"].quantity == Decimal("30")
    assert positions["AAPL"].quantity == Decimal("-30")  # short
    assert positions["NVDA"].opened_via == Position.OPENED_VIA_STRATEGY_CYCLE
    # One ledger entry per ticker, both strategy_enroll (open).
    ledger = LedgerEntry.objects.filter(portfolio=portfolio)
    assert ledger.count() == 2
    assert set(ledger.values_list("kind", flat=True)) == {LedgerEntry.KIND_STRATEGY_ENROLL}
    # Cash: long debits 30*100=3000, short credits 30*100=3000 → net 0.
    portfolio.refresh_from_db()
    assert portfolio.cash_balance == Decimal("100000.00")
    target.refresh_from_db()
    assert target.enrolled_at is not None
    assert set(target.enrollment_diff.keys()) == {"NVDA", "AAPL"}


@pytest.mark.django_db
def test_manual_mode_materializes_subset(user) -> None:
    strategy, portfolio, target = setup_target(
        user, {"NVDA": 0.03, "AAPL": 0.03}, {"NVDA": 100, "AAPL": 100},
        kind="long_only",
    )
    client = _client("ea@example.com")
    resp = client.post(
        reverse("strategy-enroll", args=[strategy.pk, target.pk]),
        {"mode": "manual", "approved_tickers": ["NVDA"]}, format="json",
    )
    assert resp.status_code == 201
    tickers = set(Position.objects.filter(portfolio=portfolio).values_list("ticker", flat=True))
    assert tickers == {"NVDA"}


@pytest.mark.django_db
def test_manual_mode_requires_approved_tickers(user) -> None:
    strategy, _, target = setup_target(user, {"NVDA": 0.03}, {"NVDA": 100}, kind="long_only")
    client = _client("ea@example.com")
    resp = client.post(
        reverse("strategy-enroll", args=[strategy.pk, target.pk]),
        {"mode": "manual"}, format="json",
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_reduce_path_writes_reduce_ledger(user) -> None:
    strategy, portfolio, target = setup_target(
        user, {"NVDA": 0.03}, {"NVDA": 100}, kind="long_only",
    )
    # Pre-existing larger position (50 sh) than the ~3%-of-NAV target → reduce.
    Position.objects.create(
        portfolio=portfolio, ticker="NVDA", quantity=Decimal("50"),
        avg_cost=Decimal("90"), opened_via=Position.OPENED_VIA_STRATEGY_CYCLE,
    )
    runs_bridge.enroll_target_into_portfolio(target, mode="auto")
    pos = Position.objects.get(portfolio=portfolio, ticker="NVDA")
    assert pos.quantity < Decimal("50")  # reduced toward the target
    reduced = Decimal("50") - pos.quantity
    led = LedgerEntry.objects.filter(portfolio=portfolio).latest("id")
    assert led.kind == LedgerEntry.KIND_STRATEGY_ENROLL_REDUCE
    # long reduce realized = (exit 100 - avg_cost 90) * reduced_qty.
    assert led.realized_pnl == (Decimal("100") - Decimal("90")) * reduced


@pytest.mark.django_db
def test_close_path_zeros_out_position(user) -> None:
    strategy, portfolio, target = setup_target(
        user, {"NVDA": 0.03}, {"NVDA": 100}, kind="long_only",
    )
    # A book position NOT in target_weights → close.
    Position.objects.create(
        portfolio=portfolio, ticker="OLD", quantity=Decimal("10"),
        avg_cost=Decimal("50"), opened_via=Position.OPENED_VIA_STRATEGY_CYCLE,
    )
    # Give OLD a fallback price via an order so it can be marked for close.
    RebalanceOrder.objects.create(
        target=target, ticker="OLD", side="sell", quantity=Decimal("1"),
        limit_price=Decimal("60"), reason="close", estimated_notional_usd=Decimal("0"),
        sequence=9,
    )
    runs_bridge.enroll_target_into_portfolio(target, mode="auto")
    assert not Position.objects.filter(portfolio=portfolio, ticker="OLD").exists()
    led = LedgerEntry.objects.filter(portfolio=portfolio, ticker="OLD").latest("id")
    assert led.kind == LedgerEntry.KIND_STRATEGY_ENROLL_CLOSE
    # close 10 sh long at (60-50) = $100 realized.
    assert led.realized_pnl == Decimal("100.00")


@pytest.mark.django_db
def test_enroll_non_done_target_rejected(user) -> None:
    strategy, _, target = setup_target(user, {"NVDA": 0.03}, {"NVDA": 100})
    PortfolioTarget.objects.filter(pk=target.pk).update(status="failed")
    target.refresh_from_db()
    with pytest.raises(runs_bridge.EnrollmentError):
        runs_bridge.enroll_target_into_portfolio(target, mode="auto")


@pytest.mark.django_db
def test_enroll_into_manual_book_rejected(user) -> None:
    strategy, portfolio, target = setup_target(user, {"NVDA": 0.03}, {"NVDA": 100})
    # Defensive: point the book at kind=manual and confirm enrollment refuses.
    Portfolio.objects.filter(pk=portfolio.pk).update(kind=Portfolio.KIND_MANUAL)
    target.strategy.portfolio.refresh_from_db()
    with pytest.raises(runs_bridge.EnrollmentError):
        runs_bridge.enroll_target_into_portfolio(target, mode="auto")
    assert Position.objects.filter(portfolio_id=portfolio.pk).count() == 0


@pytest.mark.django_db
def test_auto_enroll_on_done_hook(user) -> None:
    # _maybe_auto_enroll is the hook the cycle done-handler calls.
    from apps.portfolios.tasks import _maybe_auto_enroll

    strategy, portfolio, target = setup_target(
        user, {"NVDA": 0.03}, {"NVDA": 100}, kind="long_only",
    )
    PortfolioStrategy.objects.filter(pk=strategy.pk).update(auto_enroll_on_done=True)
    target.refresh_from_db()
    _maybe_auto_enroll(target)
    assert Position.objects.filter(portfolio=portfolio, ticker="NVDA").exists()
    target.refresh_from_db()
    assert target.enrolled_at is not None


@pytest.mark.django_db
def test_auto_enroll_off_by_default_no_positions(user) -> None:
    from apps.portfolios.tasks import _maybe_auto_enroll

    strategy, portfolio, target = setup_target(user, {"NVDA": 0.03}, {"NVDA": 100})
    _maybe_auto_enroll(target)  # auto_enroll_on_done defaults False → no-op
    assert Position.objects.filter(portfolio=portfolio).count() == 0
    target.refresh_from_db()
    assert target.enrolled_at is None
