"""Cost estimator + /api/backtests/estimate/ endpoint."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.backtests.estimator import estimate_cost
from apps.data.models import DailyBar

pytestmark = pytest.mark.django_db


def _seed_bars(tickers, n_days=10, end=dt.date(2025, 4, 1)):
    for t in tickers:
        for i in range(n_days):
            DailyBar.objects.create(
                ticker=t, date=end - dt.timedelta(days=i),
                open=100, high=101, low=99, close=100, adjusted_close=100,
                volume=1_000_000,
            )


def test_estimate_cost_basic_shape():
    _seed_bars(["AAA", "BBB"], n_days=10)
    est = estimate_cost(
        universe=["AAA", "BBB"],
        start_date=dt.date(2025, 3, 1),
        end_date=dt.date(2025, 4, 1),
        rebalance_frequency="weekly",
        max_budget_usd=Decimal("4.00"),
    )
    assert est["n_universe"] == 2
    assert est["n_rebalance_days"] >= 1
    assert est["n_invocations"] == est["n_rebalance_days"] * 2
    # Per-invocation: 4 analytical + 8 personas + 4 pipeline = 16 agents.
    assert est["n_llm_calls"] == est["n_invocations"] * 16
    assert est["est_total_usd"] > 0
    assert est["budget_cap_usd"] == 4.0
    assert est["exceeds_budget"] in (True, False)


def test_estimate_flags_budget_overrun():
    _seed_bars(["AAA"], n_days=20)
    est = estimate_cost(
        universe=["AAA"],
        start_date=dt.date(2025, 3, 1),
        end_date=dt.date(2025, 4, 1),
        rebalance_frequency="daily",
        max_budget_usd=0.01,  # impossibly low
    )
    assert est["exceeds_budget"] is True


def test_estimate_endpoint_returns_200():
    _seed_bars(["AAA"], n_days=10)
    u = User.objects.create_user(email="e@x.com", password="x")
    client = APIClient()
    client.force_authenticate(u)
    resp = client.post(
        "/api/backtests/estimate/",
        {
            "universe": ["AAA"],
            "start_date": "2025-03-01",
            "end_date": "2025-04-01",
            "rebalance_frequency": "weekly",
            "max_budget_usd": 4.0,
        },
        format="json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert "est_total_usd" in body
    assert "by_agent" in body
