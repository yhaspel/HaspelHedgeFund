"""P4 fixes:

- 1.1/1.3  broker orders default to WHOLE shares; fractional is capability- and
           opt-in-gated; per-account default persists.
- 2.1      creating a strategy auto-creates a dedicated kind=strategy book.
- 2.2      enrollment is refused when a book is shared by >1 strategy.
- 2.3      the split_shared_strategy_books command gives each strategy its book.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse
from rest_framework.test import APIClient

from apps.brokers.models import BrokerAccount
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

ORDERS_URL = "/api/broker/orders/"


@pytest.fixture(autouse=True)
def _no_live_marks():
    # Marks fall back to the RebalanceOrder limit_price → deterministic sizing.
    with patch("apps.portfolios.valuation.get_mark", return_value=None):
        yield


@pytest.fixture
def user(db):
    return User.objects.create_user(email="p4@example.com", password="x" * 12)


@pytest.fixture
def auth_client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user)
    return c


def _broker_account(user, broker: str) -> BrokerAccount:
    portfolio = Portfolio.objects.create(
        user=user, name=f"Broker {broker}", kind=Portfolio.KIND_BROKER,
        cash_balance=Decimal("100000"),
    )
    return BrokerAccount.objects.create(
        user=user, broker=broker, mode=BrokerAccount.MODE_PAPER,
        account_id=f"{broker}-{user.id}", label=f"{broker} book",
        portfolio=portfolio, connection_status=BrokerAccount.STATUS_ACTIVE,
    )


# ---------------------------------------------------------------------------
# 1.1 / 1.3 — broker order quantity policy
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_order_rounds_fractional_to_whole_by_default(auth_client, user):
    acct = _broker_account(user, "alpaca_paper")  # supports fractional, but whole is default
    resp = auth_client.post(
        ORDERS_URL,
        {"broker_account": acct.id, "ticker": "PANW", "side": "buy",
         "quantity": "24.342869", "order_type": "market"},
        format="json",
    )
    assert resp.status_code == 201, resp.data
    assert Decimal(resp.data["quantity"]) == Decimal("24")


@pytest.mark.django_db
def test_order_fractional_mode_kept_for_capable_broker(auth_client, user):
    acct = _broker_account(user, "alpaca_paper")
    resp = auth_client.post(
        ORDERS_URL,
        {"broker_account": acct.id, "ticker": "PANW", "side": "buy",
         "quantity": "24.342869", "quantity_mode": "fractional", "order_type": "market"},
        format="json",
    )
    assert resp.status_code == 201, resp.data
    assert Decimal(resp.data["quantity"]) == Decimal("24.342869")


@pytest.mark.django_db
def test_order_fractional_rejected_for_whole_only_broker(auth_client, user):
    acct = _broker_account(user, "tradestation")  # supports_fractional = False
    resp = auth_client.post(
        ORDERS_URL,
        {"broker_account": acct.id, "ticker": "PANW", "side": "buy",
         "quantity": "24.342869", "quantity_mode": "fractional", "order_type": "market"},
        format="json",
    )
    assert resp.status_code == 400
    assert "fractional" in str(resp.data).lower()


@pytest.mark.django_db
def test_order_below_one_share_in_whole_mode_rejected(auth_client, user):
    acct = _broker_account(user, "alpaca_paper")
    resp = auth_client.post(
        ORDERS_URL,
        {"broker_account": acct.id, "ticker": "BRK.A", "side": "buy",
         "quantity": "0.4", "order_type": "market"},
        format="json",
    )
    assert resp.status_code == 400
    assert "zero" in str(resp.data).lower()


@pytest.mark.django_db
def test_account_default_fractional_is_honored(auth_client, user):
    acct = _broker_account(user, "alpaca_paper")
    acct.default_quantity_mode = "fractional"
    acct.save(update_fields=["default_quantity_mode"])
    resp = auth_client.post(
        ORDERS_URL,
        {"broker_account": acct.id, "ticker": "PANW", "side": "buy",
         "quantity": "24.342869", "order_type": "market"},  # no explicit mode
        format="json",
    )
    assert resp.status_code == 201, resp.data
    assert Decimal(resp.data["quantity"]) == Decimal("24.342869")


@pytest.mark.django_db
def test_account_settings_endpoint_updates_default(auth_client, user):
    acct = _broker_account(user, "alpaca_paper")
    resp = auth_client.patch(
        reverse("broker-account-settings", args=[acct.id]),
        {"default_quantity_mode": "fractional"}, format="json",
    )
    assert resp.status_code == 200, resp.data
    acct.refresh_from_db()
    assert acct.default_quantity_mode == "fractional"
    bad = auth_client.patch(
        reverse("broker-account-settings", args=[acct.id]),
        {"default_quantity_mode": "nonsense"}, format="json",
    )
    assert bad.status_code == 400


# ---------------------------------------------------------------------------
# 2.1 — strategy creation auto-creates a dedicated book
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_create_strategy_without_portfolio_creates_dedicated_book(auth_client, user):
    universe = Universe.objects.create(name="u-auto", is_active=True)
    resp = auth_client.post(
        reverse("strategies"),
        {"name": "Concentrated long-only A", "kind": "concentrated_long",
         "universe": universe.id},
        format="json",
    )
    assert resp.status_code == 201, resp.data
    strat = PortfolioStrategy.objects.get(pk=resp.data["id"])
    assert strat.portfolio.kind == Portfolio.KIND_STRATEGY
    assert strat.portfolio.name == "Concentrated long-only A"
    assert PortfolioStrategy.objects.filter(portfolio=strat.portfolio).count() == 1


@pytest.mark.django_db
def test_two_strategies_get_separate_books(auth_client, user):
    universe = Universe.objects.create(name="u-two", is_active=True)
    a = auth_client.post(reverse("strategies"),
                         {"name": "Alpha", "universe": universe.id}, format="json")
    b = auth_client.post(reverse("strategies"),
                         {"name": "Beta", "universe": universe.id}, format="json")
    assert a.status_code == 201 and b.status_code == 201
    pa = PortfolioStrategy.objects.get(pk=a.data["id"]).portfolio_id
    pb = PortfolioStrategy.objects.get(pk=b.data["id"]).portfolio_id
    assert pa != pb


@pytest.mark.django_db
def test_create_strategy_with_explicit_portfolio_reuses_it(auth_client, user):
    universe = Universe.objects.create(name="u-reuse", is_active=True)
    book = Portfolio.objects.create(user=user, name="Reuse book", kind=Portfolio.KIND_STRATEGY)
    resp = auth_client.post(
        reverse("strategies"),
        {"name": "Gamma", "universe": universe.id, "portfolio": book.id},
        format="json",
    )
    assert resp.status_code == 201, resp.data
    assert PortfolioStrategy.objects.get(pk=resp.data["id"]).portfolio_id == book.id


# ---------------------------------------------------------------------------
# 2.2 — enrollment guardrail for shared books
# ---------------------------------------------------------------------------

def _done_target(strategy, weights, prices):
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
    return target


@pytest.mark.django_db
def test_enroll_blocked_when_book_shared(auth_client, user):
    universe = Universe.objects.create(name="u-shared", is_active=True)
    book = Portfolio.objects.create(
        user=user, name="Shared book", kind=Portfolio.KIND_STRATEGY,
        cash_balance=Decimal("100000"),
    )
    s1 = PortfolioStrategy.objects.create(
        user=user, name="S1", kind="long_only", universe=universe, portfolio=book)
    PortfolioStrategy.objects.create(
        user=user, name="S2", kind="long_only", universe=universe, portfolio=book)
    target = _done_target(s1, {"NVDA": 0.03}, {"NVDA": 100})

    preview = auth_client.get(reverse("strategy-enroll", args=[s1.pk, target.pk]))
    assert preview.status_code == 409
    assert "shared" in str(preview.data["detail"]).lower()

    apply_resp = auth_client.post(
        reverse("strategy-enroll", args=[s1.pk, target.pk]),
        {"mode": "auto"}, format="json",
    )
    assert apply_resp.status_code == 409
    assert Position.objects.filter(portfolio=book).count() == 0


@pytest.mark.django_db
def test_enroll_allowed_when_book_not_shared(auth_client, user):
    universe = Universe.objects.create(name="u-solo", is_active=True)
    book = Portfolio.objects.create(
        user=user, name="Solo book", kind=Portfolio.KIND_STRATEGY,
        cash_balance=Decimal("100000"),
    )
    s1 = PortfolioStrategy.objects.create(
        user=user, name="Solo", kind="long_only", universe=universe, portfolio=book)
    target = _done_target(s1, {"NVDA": 0.03}, {"NVDA": 100})
    resp = auth_client.post(
        reverse("strategy-enroll", args=[s1.pk, target.pk]),
        {"mode": "auto"}, format="json",
    )
    assert resp.status_code == 201, resp.data
    assert Position.objects.filter(portfolio=book, ticker="NVDA").exists()


# ---------------------------------------------------------------------------
# 2.3 — split_shared_strategy_books management command
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_split_shared_books_command(user):
    universe = Universe.objects.create(name="u-split", is_active=True)
    book = Portfolio.objects.create(user=user, name="Shared", kind=Portfolio.KIND_STRATEGY)
    s1 = PortfolioStrategy.objects.create(
        user=user, name="First", kind="long_only", universe=universe, portfolio=book)
    s2 = PortfolioStrategy.objects.create(
        user=user, name="Second", kind="long_only", universe=universe, portfolio=book)
    s3 = PortfolioStrategy.objects.create(
        user=user, name="Third", kind="long_only", universe=universe, portfolio=book)

    # Dry run mutates nothing.
    call_command("split_shared_strategy_books", stdout=StringIO())
    assert Portfolio.objects.filter(user=user, kind="strategy").count() == 1

    # Apply: earliest (s1) keeps the book; the others get their own.
    call_command("split_shared_strategy_books", "--apply", stdout=StringIO())
    s1.refresh_from_db()
    s2.refresh_from_db()
    s3.refresh_from_db()
    assert s1.portfolio_id == book.id
    assert s2.portfolio_id != book.id
    assert s3.portfolio_id != book.id
    assert s2.portfolio_id != s3.portfolio_id
    for strat in (s1, s2, s3):
        assert PortfolioStrategy.objects.filter(portfolio_id=strat.portfolio_id).count() == 1


# ---------------------------------------------------------------------------
# Hub: hide un-enrolled (empty) strategy books
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_hub_hides_empty_strategy_books_but_shows_enrolled(auth_client, user):
    universe = Universe.objects.create(name="u-hub", is_active=True)
    empty = Portfolio.objects.create(user=user, name="Empty", kind=Portfolio.KIND_STRATEGY)
    PortfolioStrategy.objects.create(
        user=user, name="EmptyS", kind="long_only", universe=universe, portfolio=empty)
    full = Portfolio.objects.create(user=user, name="Full", kind=Portfolio.KIND_STRATEGY)
    PortfolioStrategy.objects.create(
        user=user, name="FullS", kind="long_only", universe=universe, portfolio=full)
    Position.objects.create(
        portfolio=full, ticker="AAPL", quantity=Decimal("10"), avg_cost=Decimal("100"))

    resp = auth_client.get("/api/portfolios/hub/")
    assert resp.status_code == 200
    pids = {b["portfolio_id"] for b in resp.data["books"]}
    assert full.id in pids       # enrolled strategy book shows
    assert empty.id not in pids  # empty strategy book hidden


@pytest.mark.django_db
def test_hub_shows_strategy_book_with_ledger_history(auth_client, user):
    universe = Universe.objects.create(name="u-hub2", is_active=True)
    pf = Portfolio.objects.create(user=user, name="Hist", kind=Portfolio.KIND_STRATEGY)
    PortfolioStrategy.objects.create(
        user=user, name="HistS", kind="long_only", universe=universe, portfolio=pf)
    LedgerEntry.objects.create(
        portfolio=pf, kind=LedgerEntry.KIND_RECONCILE, cash_delta=Decimal("0"),
        cash_balance_after=pf.cash_balance)
    resp = auth_client.get("/api/portfolios/hub/")
    pids = {b["portfolio_id"] for b in resp.data["books"]}
    assert pf.id in pids  # ledger history alone keeps it visible


# ---------------------------------------------------------------------------
# clear_strategy_book management command
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_clear_strategy_book_flattens_positions(user):
    universe = Universe.objects.create(name="u-clear", is_active=True)
    pf = Portfolio.objects.create(
        user=user, name="Dirty", kind=Portfolio.KIND_STRATEGY, cash_balance=Decimal("1000"))
    s = PortfolioStrategy.objects.create(
        user=user, name="DirtyS", kind="long_only", universe=universe, portfolio=pf)
    Position.objects.create(
        portfolio=pf, ticker="SOXX", quantity=Decimal("10"), avg_cost=Decimal("50"))
    Position.objects.create(
        portfolio=pf, ticker="XLE", quantity=Decimal("5"), avg_cost=Decimal("80"))

    # Dry run mutates nothing.
    call_command("clear_strategy_book", "--strategy", str(s.id), stdout=StringIO())
    assert Position.objects.filter(portfolio=pf).count() == 2

    # Apply: marks fall back to avg_cost (get_mark patched to None), so cash gains
    # 10*50 + 5*80 = 900 → 1000 + 900 = 1900, positions cleared, ledger recorded.
    call_command("clear_strategy_book", "--strategy", str(s.id), "--apply", stdout=StringIO())
    pf.refresh_from_db()
    assert Position.objects.filter(portfolio=pf).count() == 0
    assert pf.cash_balance == Decimal("1900.00")
    assert LedgerEntry.objects.filter(
        portfolio=pf, kind=LedgerEntry.KIND_RECONCILE).count() == 2


@pytest.mark.django_db
def test_clear_strategy_book_refuses_non_strategy_book(user):
    pf = Portfolio.objects.create(user=user, name="Manual", kind=Portfolio.KIND_MANUAL)
    with pytest.raises(CommandError):
        call_command("clear_strategy_book", "--portfolio", str(pf.id), "--apply", stdout=StringIO())
