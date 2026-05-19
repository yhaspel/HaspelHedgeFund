"""Macro LLM calls must be attributable to the parent run/backtest.

Improvement #3 from the P2b plan: prewarm calls stay unattributed (None,
None) on purpose; calls made *inside* a council or backtest must thread
run_id / backtest_id through to record_llm_call.
"""
from __future__ import annotations

from unittest.mock import patch

from hedgefund_agents.macro import macro_agent


def test_run_macro_threads_attribution_to_compute_snapshot() -> None:
    captured: dict = {}

    def fake_compute(as_of, provider=None, *, run_id=None, backtest_id=None, state=None):
        captured["run_id"] = run_id
        captured["backtest_id"] = backtest_id

        class _S:
            as_of_date = as_of
            growth_quadrant = "expansion"
            inflation_regime = "moderate"
            yield_curve_state = "normal"
            policy_stance = "neutral"
            narrative = "n"
            sector_implications = {}

        return _S()

    with patch.object(macro_agent, "compute_snapshot", side_effect=fake_compute):
        import datetime as dt
        macro_agent.run_macro(
            {"as_of_date": dt.date(2024, 12, 31), "run_id": 42, "backtest_id": None}
        )

    assert captured == {"run_id": 42, "backtest_id": None}


def test_prewarm_call_is_intentionally_unattributed() -> None:
    """Calling compute_snapshot with neither run_id nor backtest_id is the
    shared-prewarm path; the resulting LLMCall row has both FKs null."""
    import inspect

    sig = inspect.signature(macro_agent.compute_snapshot)
    assert sig.parameters["run_id"].default is None
    assert sig.parameters["backtest_id"].default is None
