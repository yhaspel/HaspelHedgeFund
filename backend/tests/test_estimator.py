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


def test_estimate_reflects_model_overrides_p02c():
    """P02c review: changing model_overrides must change the estimate so
    the user sees the cost impact of more expensive models before they
    submit a multi-day walk-forward run."""
    _seed_bars(["AAA"], n_days=20)
    # Baseline (default models)
    base = estimate_cost(
        universe=["AAA"],
        start_date=dt.date(2025, 3, 1),
        end_date=dt.date(2025, 4, 1),
        rebalance_frequency="weekly",
        max_budget_usd=Decimal("4.00"),
    )
    # Force every analytical agent onto Sonnet 4.6 (more expensive than the
    # default Llama 70B route).
    upgraded = estimate_cost(
        universe=["AAA"],
        start_date=dt.date(2025, 3, 1),
        end_date=dt.date(2025, 4, 1),
        rebalance_frequency="weekly",
        model_overrides={
            "fundamentals": "anthropic:claude-sonnet-4-6",
            "technicals": "anthropic:claude-sonnet-4-6",
            "valuation": "anthropic:claude-sonnet-4-6",
            "sentiment": "anthropic:claude-sonnet-4-6",
        },
        max_budget_usd=Decimal("4.00"),
    )
    # The selected model for each overridden agent must show in by_agent.
    by_agent = {row["agent"]: row["model"] for row in upgraded["by_agent"]}
    assert by_agent["fundamentals"] == "anthropic:claude-sonnet-4-6"
    assert by_agent["valuation"] == "anthropic:claude-sonnet-4-6"
    # And the rolled-up cost must move (sonnet is materially more expensive
    # than llama 70B), so the user can see the difference.
    assert upgraded["est_total_usd"] != base["est_total_usd"]


def test_estimate_and_create_use_same_personas_p02c():
    """P02c review: restricting personas (e.g. to a 3-persona research
    council) at estimate time must lower the LLM-call count proportionally
    so the user can iterate on cost before submit. Estimate uses ALL
    personas when `personas=None` (matches the engine's default)."""
    _seed_bars(["AAA"], n_days=20)
    full = estimate_cost(
        universe=["AAA"],
        start_date=dt.date(2025, 3, 1),
        end_date=dt.date(2025, 4, 1),
        rebalance_frequency="weekly",
        max_budget_usd=Decimal("4.00"),
    )
    subset = estimate_cost(
        universe=["AAA"],
        start_date=dt.date(2025, 3, 1),
        end_date=dt.date(2025, 4, 1),
        rebalance_frequency="weekly",
        personas=["buffett", "munger", "graham"],
        max_budget_usd=Decimal("4.00"),
    )
    # 4 analytical + N personas + 4 pipeline agents. Reducing personas from
    # 8 to 3 cuts 5 calls per invocation.
    per_inv_full = full["n_llm_calls"] / full["n_invocations"]
    per_inv_subset = subset["n_llm_calls"] / subset["n_invocations"]
    assert per_inv_full - per_inv_subset == 5, (per_inv_full, per_inv_subset)


def test_estimate_with_all_free_models_is_zero_cost():
    """Regression: when every agent override points at an OpenRouter free
    model (price_in=0, price_out=0), the estimator must consult the
    ModelEntry catalog and return $0.00 — not fall through to the per-call
    fallback constants. Previously this produced ~$195 because PRICING.get()
    bypassed the catalog and the FALLBACK_COST_PER_CALL kicked in.
    """
    from hedgefund_agents.personas import ALL_PERSONAS

    _seed_bars(["AAA"], n_days=20)
    free = "openrouter:openai/gpt-oss-120b:free"
    overrides = {
        agent: free
        for agent in [
            "fundamentals", "technicals", "valuation", "sentiment",
            "macro", "news_digest", "risk_manager", "portfolio_manager",
            *ALL_PERSONAS,
        ]
    }
    est = estimate_cost(
        universe=["AAA"],
        start_date=dt.date(2025, 3, 1),
        end_date=dt.date(2025, 4, 1),
        rebalance_frequency="weekly",
        model_overrides=overrides,
        max_budget_usd=Decimal("4.00"),
    )
    assert est["est_total_usd"] == 0.0, est
    for row in est["by_agent"]:
        assert row["per_call_usd"] == 0.0, row
        assert row["total_usd"] == 0.0, row
        assert row["model"] == free, row


def test_compare_endpoint_returns_paired_payload_p02c():
    """P02c review: compare endpoint smoke — same shape on each side so the
    UI can render two equity curves with consistent axes. We don't need a
    real engine run; the endpoint reads stitched_oos_returns + the metrics
    serializer which both tolerate empty fold sets."""
    from apps.backtests.models import Backtest

    u = User.objects.create_user(email="c@x.com", password="x" * 12)
    bt_a = Backtest.objects.create(
        user=u, name="A", universe=["AAA"],
        start_date=dt.date(2024, 1, 1), end_date=dt.date(2024, 2, 1),
        starting_cash=Decimal("100000"),
    )
    bt_b = Backtest.objects.create(
        user=u, name="B", universe=["AAA"],
        start_date=dt.date(2024, 1, 1), end_date=dt.date(2024, 2, 1),
        starting_cash=Decimal("100000"),
    )
    client = APIClient()
    client.force_authenticate(u)
    resp = client.post(
        "/api/backtests/compare/",
        {"backtest_a_id": bt_a.id, "backtest_b_id": bt_b.id},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert set(body.keys()) == {"a", "b"}
    for side in ("a", "b"):
        assert "id" in body[side]
        assert "name" in body[side]
        assert "points" in body[side]
        assert isinstance(body[side]["points"], list)
