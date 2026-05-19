"""P2e improvements: short-side PM, strategy authz, rebalancer turnover
prioritization, PortfolioTarget cost attribution, idempotency uniqueness.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from apps.portfolios.models import (
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    Universe,
)
from apps.portfolios.rebalance import (
    CurrentPosition,
    RebalanceConfig,
    compute_orders,
)
from hedgefund_agents._persist import record_llm_call
from hedgefund_agents.llm.client import LLMResponse
from hedgefund_agents.portfolio.portfolio_manager import (
    aggregate,
    find_dissent,
    map_to_action,
)

User = get_user_model()


# --- 1. Short-side PM action mapping + dissent capture -------------------


def test_open_short_dissent_does_not_crash() -> None:
    personas = {
        "buffett": {"signal": "bearish", "confidence": 80, "thesis": "deteriorating"},
        "wood": {"signal": "bullish", "confidence": 70, "thesis": "disruptive growth"},
    }
    # The bug: previous code's signal map didn't include "open_short", so
    # find_dissent raised KeyError on a short cycle.
    dissent = find_dissent(personas, "open_short")
    names = {d.name for d in dissent}
    # Bearish aggregate → the bullish persona dissents.
    assert "wood" in names
    assert "buffett" not in names


def test_short_side_maps_bearish_aggregate_to_open_short() -> None:
    assert map_to_action(-0.5, short_side=True) == "open_short"
    assert map_to_action(0.5, short_side=True) == "hold"  # bullish signal on a short candidate


def test_aggregate_emits_open_short_for_short_side() -> None:
    personas = {
        "burry": {"signal": "bearish", "confidence": 80, "thesis": "credit risk"},
        "graham": {"signal": "bearish", "confidence": 70, "thesis": "overvalued"},
    }
    out = aggregate(
        ticker="BAD",
        persona_outputs=personas,
        risk={"max_position_pct_for_this_trade": 0.05},
        valuation={},
        pm_config={"short_side": True, "buy_threshold": 0.10, "sell_threshold": -0.10},
    )
    assert out.action == "open_short"
    assert out.target_weight_pct < 0  # signed short weight


# --- 2. Strategy authz: portfolio must belong to user --------------------


@pytest.mark.django_db
def test_cannot_create_strategy_against_another_users_portfolio() -> None:
    owner = User.objects.create_user(email="o@o.com", password="x" * 12)
    attacker = User.objects.create_user(email="a@a.com", password="x" * 12)
    pf = Portfolio.objects.create(user=owner, name="owner book")
    uni = Universe.objects.create(name="t-uni", description="d")

    c = APIClient()
    tok = c.post(
        reverse("login"), {"email": "a@a.com", "password": "x" * 12}, format="json",
    ).data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}")
    resp = c.post(
        reverse("strategies"),
        {
            "name": "steal",
            "universe": uni.id,
            "portfolio": pf.id,
        },
        format="json",
    )
    assert resp.status_code == 400, resp.data
    assert "portfolio" in resp.data
    # And no strategy was created against the owner's book.
    assert not PortfolioStrategy.objects.filter(portfolio=pf, user=attacker).exists()


# --- 3. Rebalancer prioritization under turnover cap --------------------


def test_turnover_cap_prioritizes_risk_off_orders() -> None:
    cfg = RebalanceConfig(
        portfolio_value=100_000.0,
        last_close={"OLD": 100.0, "NEW": 50.0},
        max_turnover_pct=0.05,  # $5k cap on $100k book — restrictive
    )
    # 20k notional close + 20k notional open. Cap is 5k.
    # Risk-off is exempt → full $20k close runs. Risk-on scales to 5k.
    current = [CurrentPosition(ticker="OLD", quantity=200.0, avg_cost=80.0)]
    target = {"NEW": 0.20}  # target 20% open = $20k buy
    orders = compute_orders(current, target, cfg)
    closes = [o for o in orders if o.reason == "close"]
    opens = [o for o in orders if o.reason == "open"]
    assert closes, "close order must remain when cap binds"
    # Risk-off survives unscaled — closing exposure is exempt.
    assert abs(closes[0].estimated_notional_usd - 20_000.0) < 1.0, (
        f"close should be fully funded, got {closes[0].estimated_notional_usd}"
    )
    # Risk-on scales down to the cap.
    assert opens, "open should remain (scaled), not be dropped"
    assert abs(opens[0].estimated_notional_usd - 5_000.0) < 1.0, (
        f"open should be scaled to cap, got {opens[0].estimated_notional_usd}"
    )


# --- 4. Cost attribution to PortfolioTarget ------------------------------


@pytest.mark.django_db
def test_record_llm_call_bumps_portfolio_target_cost() -> None:
    u = User.objects.create_user(email="c@c.com", password="x" * 12)
    uni = Universe.objects.create(name="u")
    pf = Portfolio.objects.create(user=u, name="b")
    strat = PortfolioStrategy.objects.create(user=u, universe=uni, portfolio=pf, name="s")
    target = PortfolioTarget.objects.create(
        strategy=strat, as_of_date=dt.date(2024, 12, 31),
        total_cost_usd=Decimal("0"),
    )
    resp = LLMResponse(
        text="ok",
        model="claude-sonnet-4-6",
        provider="anthropic",
        prompt_tokens=1000, completion_tokens=200,
        cost_usd=0.12,
    )
    record_llm_call(
        run_id=None, agent_name="buffett", resp=resp,
        portfolio_target_id=target.pk,
    )
    target.refresh_from_db()
    assert float(target.total_cost_usd) == pytest.approx(0.12, abs=1e-6)


# --- 5. Race-safe idempotency: unique constraint ------------------------


@pytest.mark.django_db
def test_portfolio_target_unique_per_strategy_per_day() -> None:
    from django.db import IntegrityError
    u = User.objects.create_user(email="i@i.com", password="x" * 12)
    uni = Universe.objects.create(name="u2")
    pf = Portfolio.objects.create(user=u, name="b2")
    strat = PortfolioStrategy.objects.create(user=u, universe=uni, portfolio=pf, name="s2")
    PortfolioTarget.objects.create(strategy=strat, as_of_date=dt.date(2024, 12, 31))
    with pytest.raises(IntegrityError):
        PortfolioTarget.objects.create(strategy=strat, as_of_date=dt.date(2024, 12, 31))
