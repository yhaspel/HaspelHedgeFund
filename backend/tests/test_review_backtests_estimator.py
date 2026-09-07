"""Adversarial review (reviewer: backtests) — estimator vs. what the graph does.

estimator.estimate_cost used to count, per (ticker, rebalance-day) invocation,
one LLM call for each of: 4 analytical + P personas + macro + news_digest +
risk_manager + portfolio_manager  ==> 8 + P calls.

What council.py actually issues per invocation (disable_cio=True default):
  fundamentals, technicals, valuation, sentiment, news_digest, risk_manager,
  P personas                                            ==> 6 + P calls
  macro: ONE call per unique as_of_date (MacroSnapshot cache, macro_agent.py:131)
  portfolio_manager: ZERO calls (deterministic aggregate(), portfolio_manager.py:339-359)
  cio: +1 per invocation, only when the run opts in (disable_cio=False)

So the call count and $ were over-stated by (2 - 1/n_universe) calls per
invocation, and an opted-in CIO was not modelled at all.

FIXED by WP B3a §3: macro is billed per unique day, portfolio_manager is not
billed, CIO is billed on opt-in, and the response carries ``n_primes`` (the
unique ticker-days the prime phase walks across ALL folds). The L2
LLMResponseCache discount (a re-run over the same window and agent versions
costs ~$0) is still not modelled — deliberately conservative.
"""
from __future__ import annotations

import datetime as dt
import inspect
from decimal import Decimal

import pytest

from apps.backtests.estimator import estimate_cost
from apps.data.models import DailyBar

pytestmark = pytest.mark.django_db


def _seed(universe, days):
    for t in universe:
        for d in days:
            DailyBar.objects.create(ticker=t, date=d, source="fmp", open=1, high=1, low=1,
                                    close=1, adjusted_close=1, volume=1)


def test_estimator_bills_macro_per_day_and_never_bills_the_pm_node():
    start = dt.date(2024, 1, 1)
    days = [start + dt.timedelta(days=i) for i in range(14)]
    days = [d for d in days if d.weekday() < 5]  # 10 trading days
    universe = ["AAA", "BBB", "CCC"]
    _seed(universe, days)
    est = estimate_cost(
        universe=universe, start_date=start, end_date=days[-1],
        rebalance_frequency="daily", personas=["buffett"], max_budget_usd=Decimal("4"),
    )
    n_inv = len(days) * len(universe)  # 30
    assert est["n_invocations"] == n_inv
    # n_primes == unique ticker-days across the whole master window (== all folds).
    assert est["n_primes"] == n_inv
    # 4 analytical + 1 persona + news_digest + risk_manager, per invocation,
    # plus ONE macro call per unique day. PM is not an LLM call at all.
    assert est["n_llm_calls"] == n_inv * 7 + len(days)
    agents = {row["agent"] for row in est["by_agent"]}
    assert "portfolio_manager" not in agents
    assert "cio" not in agents                                # disable_cio default
    macro_line = next(r for r in est["by_agent"] if r["agent"] == "macro")
    assert macro_line["n_calls"] == len(days)
    assert macro_line["total_usd"] == pytest.approx(
        macro_line["per_call_usd"] * len(days), rel=1e-9
    )
    persona_line = next(r for r in est["by_agent"] if r["agent"] == "buffett")
    assert persona_line["n_calls"] == n_inv

    # Structural proof that the PM node is not an LLM call.
    from hedgefund_agents.portfolio import portfolio_manager as pm_mod
    src = inspect.getsource(pm_mod.run_portfolio_manager)
    assert "call_structured" not in src and "get_llm" not in src


def test_estimator_bills_cio_only_when_the_run_opts_in():
    start = dt.date(2024, 1, 1)
    days = [start + dt.timedelta(days=i) for i in range(14)]
    days = [d for d in days if d.weekday() < 5]
    universe = ["AAA"]
    _seed(universe, days)
    kw = dict(universe=universe, start_date=start, end_date=days[-1],
              rebalance_frequency="daily", personas=["buffett"],
              max_budget_usd=Decimal("4"))
    off = estimate_cost(**kw, disable_cio=True)
    on = estimate_cost(**kw, disable_cio=False)
    assert "cio" not in {r["agent"] for r in off["by_agent"]}
    assert "cio" in {r["agent"] for r in on["by_agent"]}
    assert on["n_llm_calls"] - off["n_llm_calls"] == on["n_primes"]
    assert on["est_total_usd"] >= off["est_total_usd"]


def test_sentiment_node_never_calls_llm_in_council_state_but_is_billed():
    """council.py never sets state['news'] (only apps/persona_evolution does), so
    run_sentiment short-circuits to score 0.0 with ZERO LLM calls — for live runs
    and backtests alike — while the estimator bills one 'sentiment' call per
    invocation."""
    from unittest.mock import patch

    from hedgefund_agents.analytical import sentiment as s_mod

    state = {"ticker": "AAA", "as_of_date": dt.date(2024, 1, 2), "backtest_id": 1}
    with patch.object(s_mod, "call_structured") as cs:
        out = s_mod.run_sentiment(state)
    assert out == {"sentiment": {"score": 0.0, "top_drivers": []}}
    assert not cs.called
