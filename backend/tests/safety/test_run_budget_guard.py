"""P5-SH WS1.2 — mid-run LLM-spend guard for ad-hoc/scheduled runs.

The guard sums a run's LLMCall cost between agent nodes (in ``record_llm_call``)
and aborts the run FAILED/"budget_exceeded" once it crosses the effective cap —
the run's own ``max_budget_usd`` or, when NULL, ``RUN_DEFAULT_MAX_BUDGET_USD``.
BudgetExceeded must propagate through the self-heal node wrapper (not degrade to a
null signal), exactly like ModelUnavailable.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model

from apps.backtests.exceptions import BudgetExceeded
from hedgefund_agents._persist import record_llm_call
from hedgefund_agents.llm.client import LLMResponse
from hedgefund_agents.models import LLMCall


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(
        email="op@example.test", password="x", is_superuser=True,
    )


def _resp(cost: float) -> LLMResponse:
    return LLMResponse(text="{}", model="m", provider="fake",
                       prompt_tokens=1, completion_tokens=1, cost_usd=cost)


def _run(user, **kw):
    from apps.runs.models import Run

    return Run.objects.create(
        user=user, tickers=["AAPL"], as_of_date=dt.date(2026, 6, 1),
        status=Run.QUEUED, **kw,
    )


def test_record_llm_call_raises_when_over_run_cap(user):
    run = _run(user, max_budget_usd=Decimal("0.50"))
    with pytest.raises(BudgetExceeded) as ei:
        record_llm_call(run_id=run.id, agent_name="buffett", resp=_resp(0.60))
    assert ei.value.spent >= 0.50 and ei.value.cap == 0.50
    # The call row is persisted BEFORE the abort, so partial cost is preserved.
    assert LLMCall.objects.filter(run_id=run.id).count() == 1
    run.refresh_from_db()
    assert run.total_cost_usd == Decimal("0.600000")


def test_env_default_cap_applies_when_run_cap_null(user, settings):
    settings.RUN_DEFAULT_MAX_BUDGET_USD = Decimal("0.50")
    run = _run(user, max_budget_usd=None)
    with pytest.raises(BudgetExceeded):
        record_llm_call(run_id=run.id, agent_name="buffett", resp=_resp(0.60))


def test_guard_is_off_when_no_cap_configured(user, settings):
    settings.RUN_DEFAULT_MAX_BUDGET_USD = None
    run = _run(user, max_budget_usd=None)
    # Well over any sane per-run spend, but no cap → no abort.
    record_llm_call(run_id=run.id, agent_name="buffett", resp=_resp(5.00))
    run.refresh_from_db()
    assert run.total_cost_usd == Decimal("5.000000")


def test_under_cap_does_not_abort(user):
    run = _run(user, max_budget_usd=Decimal("1.00"))
    record_llm_call(run_id=run.id, agent_name="buffett", resp=_resp(0.40))
    run.refresh_from_db()
    assert run.total_cost_usd == Decimal("0.400000")


def test_wrapper_reraises_budget_exceeded_even_in_tolerant_context():
    """Self-heal degrades most node failures to a null signal, but a budget
    breach must propagate so the run stops spending — like ModelUnavailable."""
    from hedgefund_agents.graphs._node_fallback import wrap_backtest_tolerant

    def _node(state):
        raise BudgetExceeded(0.10, 0.05, 1, 1)

    wrapped = wrap_backtest_tolerant(_node, "buffett")
    with pytest.raises(BudgetExceeded):
        wrapped({"backtest_id": 1, "ticker": "AAPL"})  # tolerant/backtest context


def test_execute_run_aborts_failed_budget_exceeded(user):
    """End to end: a node's LLM call crosses the cap mid-run → execute_run ends
    the run FAILED with a 'budget_exceeded' message and the partial cost stored."""
    from apps.runs.models import Run
    from apps.runs.tasks import execute_run

    run = _run(user, max_budget_usd=Decimal("0.05"))

    def _spend(state):
        record_llm_call(run_id=run.id, agent_name="buffett", resp=_resp(0.10))
        return {}

    graph = MagicMock()
    graph.invoke.side_effect = _spend
    with patch("apps.runs.tasks.resolve_graph", return_value=graph), \
         patch("apps.runs.tasks.get_fmp_provider", return_value=MagicMock()), \
         patch("apps.runs.tasks.get_edgar_provider", return_value=MagicMock()), \
         patch("apps.runs.tasks.get_ownership_provider", return_value=MagicMock()):
        with pytest.raises(BudgetExceeded):
            execute_run(run.id)

    run.refresh_from_db()
    assert run.status == Run.FAILED
    assert run.error_message.startswith("budget_exceeded")
    assert run.total_cost_usd == Decimal("0.100000")
