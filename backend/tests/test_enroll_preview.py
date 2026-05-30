"""P4 WS-E: enrollment preview.

Marks resolve via the RebalanceOrder limit_price fallback (no FMP key in
tests), so sizing is deterministic. Verifies row shape, actions, signed
weights, and the min_trade_notional skip.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from apps.portfolios.models import (
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    RebalanceOrder,
    Universe,
)

User = get_user_model()


@pytest.fixture(autouse=True)
def _no_live_marks():
    """Force the limit_price fallback so sizing is deterministic (no live FMP)."""
    with patch("apps.portfolios.valuation.get_mark", return_value=None):
        yield


@pytest.fixture
def user(db):
    return User.objects.create_user(email="ep@example.com", password="supersecret")


def _client(email: str) -> APIClient:
    c = APIClient()
    token = c.post(
        reverse("login"), {"email": email, "password": "supersecret"}, format="json"
    ).data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return c


def setup_target(
    user, weights: dict, prices: dict, *, kind="long_short",
    cash=Decimal("100000"), min_trade=Decimal("250"), status="done",
) -> PortfolioTarget:
    universe = Universe.objects.create(name=f"ep-{user.id}-{kind}-{status}", is_active=True)
    portfolio = Portfolio.objects.create(
        user=user, name="book", kind="strategy", cash_balance=cash,
    )
    strategy = PortfolioStrategy.objects.create(
        user=user, name="s", kind=kind, universe=universe, portfolio=portfolio,
        min_trade_notional_usd=min_trade,
    )
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date(2026, 5, 29), status=status,
        target_weights=weights,
        gross_pct=Decimal(str(round(sum(abs(w) for w in weights.values()), 4))),
        net_pct=Decimal(str(round(sum(weights.values()), 4))),
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
def test_preview_long_only_rows(user) -> None:
    target = setup_target(
        user, {"NVDA": 0.03, "AAPL": 0.03}, {"NVDA": 100, "AAPL": 200},
        kind="long_only",
    )
    client = _client("ep@example.com")
    resp = client.get(reverse("strategy-enroll", args=[target.strategy_id, target.pk]))
    assert resp.status_code == 200
    rows = {r["ticker"]: r for r in resp.data["rows"]}
    # 3% of 100k = $3000; whole shares at $100 → 30, at $200 → 15.
    assert rows["NVDA"]["side"] == "long"
    assert rows["NVDA"]["action"] == "open"
    assert rows["NVDA"]["target_notional_usd"] == "3000.00"
    assert rows["NVDA"]["suggested_quantity"] == "30"
    assert rows["NVDA"]["mark_source"] == "limit_price"
    assert rows["AAPL"]["suggested_quantity"] == "15"
    assert resp.data["totals"]["n_open"] == 2
    assert resp.data["portfolio"]["positions_count"] == 0


@pytest.mark.django_db
def test_preview_long_short_signed(user) -> None:
    target = setup_target(
        user, {"NVDA": 0.03, "AAPL": -0.03}, {"NVDA": 100, "AAPL": 100},
    )
    client = _client("ep@example.com")
    resp = client.get(reverse("strategy-enroll", args=[target.strategy_id, target.pk]))
    rows = {r["ticker"]: r for r in resp.data["rows"]}
    assert rows["NVDA"]["side"] == "long"
    assert rows["NVDA"]["suggested_quantity"] == "30"
    assert rows["AAPL"]["side"] == "short"
    assert rows["AAPL"]["suggested_quantity"] == "-30"  # signed short
    assert abs(resp.data["totals"]["net_pct"]) < 1e-9


@pytest.mark.django_db
def test_preview_below_min_trade_notional_skips(user) -> None:
    # min_trade $5000 > a 3% ($3000) position → skip.
    target = setup_target(
        user, {"NVDA": 0.03}, {"NVDA": 100}, min_trade=Decimal("5000"),
    )
    client = _client("ep@example.com")
    resp = client.get(reverse("strategy-enroll", args=[target.strategy_id, target.pk]))
    row = resp.data["rows"][0]
    assert row["action"] == "skip"
    assert "below_min_trade_notional" in row["warnings"]


@pytest.mark.django_db
def test_preview_non_done_target_409(user) -> None:
    target = setup_target(user, {"NVDA": 0.03}, {"NVDA": 100}, status="failed")
    client = _client("ep@example.com")
    resp = client.get(reverse("strategy-enroll", args=[target.strategy_id, target.pk]))
    assert resp.status_code == 409


@pytest.mark.django_db
def test_preview_other_users_target_404(user) -> None:
    target = setup_target(user, {"NVDA": 0.03}, {"NVDA": 100})
    User.objects.create_user(email="intruder@example.com", password="supersecret")
    client = _client("intruder@example.com")
    resp = client.get(reverse("strategy-enroll", args=[target.strategy_id, target.pk]))
    assert resp.status_code == 404
