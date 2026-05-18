"""Budget kill-switch: prime_agent_cache aborts when total_cost_usd >= cap.

We monkeypatch graph.invoke + record_llm_call to simulate cost accrual without
hitting any LLM provider.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.accounts.models import User
from apps.backtests import engine as engine_mod
from apps.backtests.exceptions import BudgetExceeded
from apps.backtests.models import Backtest
from apps.backtests.tasks import run_backtest

pytestmark = pytest.mark.django_db


def _make_bt(cap: Decimal) -> Backtest:
    user = User.objects.create_user(email="b@x.com", password="x")
    return Backtest.objects.create(
        user=user, name="cap test",
        universe=["AAA", "BBB"],
        start_date=dt.date(2025, 1, 6), end_date=dt.date(2025, 1, 10),
        starting_cash=Decimal("100000"),
        is_window_days=5, oos_window_days=2, step_days=2,
        n_candidates=1, max_budget_usd=cap,
    )


def test_prime_agent_cache_aborts_when_budget_exhausted(monkeypatch):
    bt = _make_bt(cap=Decimal("0.50"))

    # Pretend the universe has bars on these dates so trading_days returns them.
    days = [dt.date(2025, 1, 6), dt.date(2025, 1, 7), dt.date(2025, 1, 8)]
    monkeypatch.setattr(engine_mod, "trading_days", lambda *a, **k: days)

    # Pretend rebalance is daily.
    monkeypatch.setattr(
        engine_mod, "rebalance_dates_for", lambda days_, freq: set(days_),
    )

    # Fake graph: each invoke bumps the backtest's total_cost_usd by $0.30.
    invocations = {"n": 0}

    class FakeGraph:
        def invoke(self, state):
            invocations["n"] += 1
            Backtest.objects.filter(pk=bt.pk).update(
                total_cost_usd=Decimal("0.30") * invocations["n"]
            )
            return {}

    monkeypatch.setattr(
        "hedgefund_agents.graphs.council.build_council_graph",
        lambda personas=None: FakeGraph(),
    )
    monkeypatch.setattr(
        "hedgefund_agents.versioning.ensure_versions_synced", lambda: None
    )
    monkeypatch.setattr(
        "hedgefund_agents.versioning.snapshot_versions", lambda *_a, **_k: {}
    )
    monkeypatch.setattr(
        "hedgefund_agents.registry.get_data_provider", lambda: object()
    )
    monkeypatch.setattr(
        "hedgefund_agents.registry.get_filings_provider", lambda: object()
    )
    # close_old_connections() inside the loop tears down the test transaction.
    monkeypatch.setattr("django.db.close_old_connections", lambda: None)

    with pytest.raises(BudgetExceeded) as exc_info:
        engine_mod.prime_agent_cache(
            bt=bt, start=days[0], end=days[-1], rebalance_freq="daily",
        )
    # After 2 invocations spent = $0.60 >= cap $0.50; third invocation blocked.
    assert exc_info.value.cap == 0.50
    assert exc_info.value.spent >= 0.50
    assert invocations["n"] == 2


def test_run_backtest_task_sets_aborted_budget_status(monkeypatch):
    bt = _make_bt(cap=Decimal("4.00"))

    def boom(_bt):
        raise BudgetExceeded(spent=4.5, cap=4.0, done=10, total=100)

    monkeypatch.setattr("apps.backtests.walkforward.run_walkforward", boom)

    with pytest.raises(BudgetExceeded):
        run_backtest(bt.pk)
    bt.refresh_from_db()
    assert bt.status == Backtest.ABORTED_BUDGET
    assert "BudgetExceeded" in bt.error_message
