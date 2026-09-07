"""Adversarial review (reviewer: fund) — batch 3: NAV proxy with shorts,
daily-cap notional placeholder, bootstrap re-run vs a renamed fund, composite
weights parsing.

Run:
  DJANGO_SETTINGS_MODULE=hedgefund.settings.test /tmp/v312/bin/pytest \
      tests/test_review_fund_misc.py -q -p no:cacheprovider
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from apps.brokers.adapters import mock as mock_adapter
from apps.brokers.models import BrokerAccount, BrokerOrder
from apps.portfolios import autopilot as bridge
from apps.portfolios import autopilot_risk
from apps.portfolios.models import (
    AutonomousFund,
    AutopilotRun,
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    Position,
    RebalanceOrder,
    StrategyAutopilot,
    Universe,
    UniverseMembership,
)

User = get_user_model()


@pytest.fixture(autouse=True)
def _reset_mock():
    mock_adapter.reset_state()
    yield
    mock_adapter.reset_state()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="rev-fund3@x.test", password="pw-fake-123456789")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    c.raise_request_exception = False
    return c


# ===========================================================================
# F14 (FIXED) — the cost-basis NAV proxy (autopilot_risk.book_nav /
# sleeves.book_value fallback) ADDED |qty|×avg_cost for a short position even
# though the short proceeds already sit in cash → NAV overstated by 2× the
# short notional. It is the denominator of the submit-time max_position_pct
# gate, so on a long/short sleeve the cap loosened with every short opened.
# The quantity is signed now: a short subtracts its buy-back liability.
# ===========================================================================
def test_F14_cost_basis_nav_proxy_double_counts_shorts(user):
    pf = Portfolio.objects.create(
        user=user, kind=Portfolio.KIND_SLEEVE, name="s",
        cash_balance=Decimal("150000"),                       # $100k + $50k short proceeds
    )
    Position.objects.create(
        portfolio=pf, ticker="XLE", quantity=Decimal("-500"), avg_cost=Decimal("100"),
    )
    # True NAV: 150k cash − 50k short liability = 100k.
    assert autopilot_risk.book_nav(pf) == Decimal("100000")

    strategy = PortfolioStrategy.objects.create(
        user=user, name="S", universe=Universe.objects.create(name="rev3-u"),
        portfolio=Portfolio.objects.create(user=user, kind=Portfolio.KIND_STRATEGY, name="b"),
        max_position_pct=Decimal("0.10"),
    )
    acct_pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_BROKER, name="bk")
    acct = BrokerAccount.objects.create(
        user=user, broker="alpaca_paper", mode=BrokerAccount.MODE_PAPER, account_id="x",
        label="L", portfolio=acct_pf, connection_status=BrokerAccount.STATUS_ACTIVE,
    )
    check = autopilot_risk.make_risk_check(strategy, book=pf)
    # $15k of AAPL = 15% of the true $100k NAV → rejected at the 10% cap (it
    # used to pass, reading as 7.5% of the inflated $200k proxy).
    order = BrokerOrder.objects.create(
        broker_account=acct, ticker="AAPL", side="buy", quantity=Decimal("150"),
        limit_price=Decimal("100"),
    )
    reasons = check(order)
    assert len(reasons) == 1 and "max_position_pct" in reasons[0]
    # A $9k order (9% of the true NAV) still passes — the cap did not tighten.
    ok = BrokerOrder.objects.create(
        broker_account=acct, ticker="MSFT", side="buy", quantity=Decimal("90"),
        limit_price=Decimal("100"),
    )
    assert check(ok) == []


# ===========================================================================
# F15 (FIXED) — the daily notional cap counted unfilled market orders at a flat
# $100 placeholder (no limit, no fill yet): a $650 SPY order counted as $100/sh,
# a $30 ETF as $100/sh. The basis now prices the order off the row it was SIZED
# against (its RebalanceOrder's estimated notional), and only falls back to the
# placeholder when nothing else is known.
# ===========================================================================
def test_F15_daily_notional_cap_uses_100_dollar_placeholder_for_unfilled_orders(user):
    acct_pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_BROKER, name="bk")
    acct = BrokerAccount.objects.create(
        user=user, broker="alpaca_paper", mode=BrokerAccount.MODE_PAPER, account_id="y",
        label="L", portfolio=acct_pf, connection_status=BrokerAccount.STATUS_ACTIVE,
    )
    strategy = PortfolioStrategy.objects.create(
        user=user, name="S", universe=Universe.objects.create(name="rev3-u2"),
        portfolio=Portfolio.objects.create(user=user, kind=Portfolio.KIND_STRATEGY, name="b2"),
    )
    ap = StrategyAutopilot.objects.create(strategy=strategy, broker_account=acct)
    run = AutopilotRun.objects.create(autopilot=ap, fire_time_utc=timezone.now())
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=dt.date(2026, 9, 4), status=PortfolioTarget.DONE,
    )
    ro = RebalanceOrder.objects.create(
        target=target, ticker="SPY", side="buy", quantity=Decimal("100"),
        reason="open", estimated_notional_usd=Decimal("65000"), sequence=1,
    )
    o = BrokerOrder.objects.create(
        broker_account=acct, ticker="SPY", side="buy", quantity=Decimal("100"),
        rebalance_order=ro,
        order_type="market", status=BrokerOrder.STATUS_SUBMITTED,   # live, not yet filled
    )
    run.broker_orders.add(o)
    n, notional = bridge._prior_24h(acct)
    assert n == 1
    assert notional == Decimal("65000")           # the $650/sh it was sized at, not $100


# ===========================================================================
# F16 (FIXED) — bootstrap re-run after the owner renamed the fund. The command
# used to key the fund on name="Autonomous Fund", so a rename made the re-run
# create a SECOND fund for the same owner (bound to the same shared broker
# account) while the API resolved the user's fund as "first by id" — the two
# halves of the product disagreed about which fund was live. _upsert_fund now
# resolves the fund the same way _user_fund does, so the re-run is idempotent.
# ===========================================================================
def test_F16_bootstrap_after_rename_creates_a_second_fund(user, settings):
    for slug, tickers in (
        ("sp500_top_200", ["AAPL"]), ("sector_etfs", ["XLK"]), ("macro_etfs", ["SPY"]),
    ):
        u, _ = Universe.objects.get_or_create(name=slug)
        if not UniverseMembership.objects.filter(universe=u).exists():
            for t in tickers:
                UniverseMembership.objects.create(
                    universe=u, ticker=t, effective_from=dt.date(2020, 1, 1),
                )
    settings.ALPACA_PAPER_ACCOUNTS = [{"slot": 1, "name": "pool", "key_id": "K", "secret": "S"}]
    settings.ALPACA_FUND_OWNER_EMAIL = user.email
    call_command("bootstrap_autonomous_fund", broker="mock", no_verify=True)
    fund = AutonomousFund.objects.get(owner=user)
    fund.name = "Yuval's Fund"                     # PUT /api/fund/ {name}
    fund.save()
    # The re-run is idempotent now: it finds the SAME fund (first by id, name
    # irrelevant), so there is no second fund and no FundError from the
    # template strategies already having a sleeve in the renamed one.
    call_command("bootstrap_autonomous_fund", broker="mock", no_verify=True)
    funds = AutonomousFund.objects.filter(owner=user).order_by("id")
    assert funds.count() == 1
    only = funds.first()
    assert only.id == fund.id
    assert only.name == "Yuval's Fund"              # the rename survives the re-run
    assert only.broker_account_id == fund.broker_account_id
    from apps.portfolios.api_fund import _user_fund

    assert _user_fund(user).id == fund.id           # API and command agree
    r = APIClient()
    r.force_authenticate(user)
    assert r.get("/api/fund/accounts/").json()["accounts"][0]["in_fund"] is True


# ===========================================================================
# F17 (FIXED) — /api/fund/composite/?weights=<id>:nan → NaN weights used to
# survive parsing (the `w < 0` guard is false for NaN); DRF's strict JSON
# renderer then 500'd. Non-finite weights are rejected, so the caller falls
# back to the equal-weight default.
# ===========================================================================
def test_F17_composite_nan_weight(client, user):
    AutonomousFund.objects.create(owner=user, name="F")
    r = client.get("/api/fund/composite/?weights=1:nan")
    assert r.status_code == 200
    from apps.portfolios.fund_composite import _parse_weights

    assert _parse_weights("1:nan,2:1", [1, 2]) is None
    assert _parse_weights("1:inf,2:1", [1, 2]) is None
    assert _parse_weights("1:-inf,2:1", [1, 2]) is None
    # A well-formed set still normalizes.
    assert _parse_weights("1:3,2:1", [1, 2]) == {1: 0.75, 2: 0.25}


# ===========================================================================
# F18 (FIXED) — the §9 gate was evidence-by-association: it accepted the
# strategy's latest DONE total-return backtest without checking that the
# backtest's universe / engine / window matched the strategy. A backtest of a
# different universe (or engine, or a cherry-picked 18-month window — all
# caller-supplied on POST /api/backtests/) armed the strategy. ``enable_gate``
# now blocks it; the same checks surface as non-blocking warnings elsewhere.
# ===========================================================================
def test_F18_gate_accepts_a_backtest_of_a_different_universe_and_engine(user, client):
    from apps.backtests.models import Backtest, BacktestMetrics
    from apps.portfolios.validation import enable_gate, validation_status

    uni = Universe.objects.create(name="rev3-rp")
    for t in ("SPY", "TLT", "GLD"):
        UniverseMembership.objects.create(
            universe=uni, ticker=t, effective_from=dt.date(2010, 1, 1),
        )
    strategy = PortfolioStrategy.objects.create(
        user=user, name="RP", universe=uni, kind=PortfolioStrategy.KIND_RISK_PARITY,
        portfolio=Portfolio.objects.create(user=user, kind=Portfolio.KIND_STRATEGY, name="rp"),
    )
    StrategyAutopilot.objects.create(strategy=strategy)
    # Caller-chosen universe / engine / window (the create serializer takes
    # `universe`, `engine_mode`, `search_space`, dates from the request body).
    bt = Backtest.objects.create(
        user=user, strategy=strategy, name="NVDA only, council engine, 2023 bull run",
        universe=["NVDA"], engine_mode="council",
        search_space={"vol_target_annual": 0.05, "lookback": 5},
        start_date=dt.date(2023, 1, 1), end_date=dt.date(2024, 7, 1), status=Backtest.DONE,
    )
    BacktestMetrics.objects.create(
        backtest=bt, mean_oos_sharpe=Decimal("2.5"), max_drawdown_pct=Decimal("3.0"),
        sharpe=Decimal("2.1"),
    )
    # The metric checks still pass — they say nothing about evidence FIT ...
    out = validation_status(strategy)
    assert out["passed"] is True and out["backtest_id"] == bt.id
    # ... but they are surfaced as warnings, and the enable gate blocks.
    keys = lambda res: {c["key"] for c in res["checks"] if not c["ok"]}  # noqa: E731
    gate = enable_gate(strategy)
    assert gate["passed"] is False
    # NVDA ≠ SPY/TLT/GLD, council ≠ risk_parity, and an 18-month window at the
    # default 252/63/63 walk-forward is only 5 folds (needs ≥ 6).
    assert keys(gate) == {"universe_matches", "engine_matches_kind", "enough_folds"}
    assert len(out["warnings"]) == 3 and gate["reasons"] == out["warnings"]

    r = client.post(f"/api/strategies/{strategy.id}/autopilot/enable/")
    assert r.status_code == 409, r.json()
    assert any("universe" in reason for reason in r.json()["reasons"])
    strategy.autopilot.refresh_from_db()
    assert strategy.autopilot.is_enabled is False
