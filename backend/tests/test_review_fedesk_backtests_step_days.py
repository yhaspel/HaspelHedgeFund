"""Review (fedesk) — the New-backtest form let step_days/oos_window_days/
starting_cash through unchecked (frontend `min=` attributes are advisory and
the page never checks form validity), and the backend contract did not stop
them either. step_days <= 0 made generate_folds() never terminate, which pinned
a Celery worker at 100% CPU with an ever-growing folds list.

FIXED by WP B3a §1: BacktestCreateSerializer enforces every bound below, and
generate_folds raises ValueError rather than spinning. The first two tests
already asserted the desired contract and now pass; the third asserts the loop
is gone (bounded by the same 0.5s itimer, which must NOT fire).
"""
from __future__ import annotations

import datetime as dt
import signal

import pytest

from apps.backtests.serializers import BacktestCreateSerializer
from apps.backtests.walkforward import generate_folds

BASE = {
    "name": "wf",
    "universe": ["AAPL", "MSFT"],
    "start_date": "2023-01-02",
    "end_date": "2025-12-31",
    "is_window_days": 252,
    "oos_window_days": 63,
    "n_candidates": 50,
    "is_objective": "sharpe",
    "baseline": "universe_ew",
    "rebalance_frequency": "weekly",
    "max_budget_usd": "4.00",
}


@pytest.mark.django_db
@pytest.mark.parametrize("step_days", [0, -21])
def test_create_serializer_should_reject_non_positive_step_days(step_days: int) -> None:
    ser = BacktestCreateSerializer(data={**BASE, "step_days": step_days})
    # Desired: a 400 at the API boundary. On HEAD this assertion FAILS —
    # is_valid() returns True and the row would be created + dispatched.
    assert not ser.is_valid(), f"step_days={step_days} accepted: {ser.validated_data}"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "field,value",
    [("starting_cash", "0"), ("oos_window_days", 0), ("n_candidates", 0), ("commission_bps", "-5")],
)
def test_create_serializer_should_reject_degenerate_params(field: str, value) -> None:
    ser = BacktestCreateSerializer(data={**BASE, field: value})
    assert not ser.is_valid(), f"{field}={value!r} accepted: {ser.validated_data}"


class _Timeout(Exception):
    pass


def test_generate_folds_raises_immediately_for_step_days_zero() -> None:
    """The itimer is the proof the loop is gone: generate_folds must fail fast
    with ValueError, well inside 0.5s, instead of spinning until _Timeout."""
    def _alarm(signum, frame):  # noqa: ARG001
        raise _Timeout

    old = signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, 0.5)
    try:
        with pytest.raises(ValueError, match="step_days must be >= 1"):
            generate_folds(
                start=dt.date(2023, 1, 2), end=dt.date(2025, 12, 31),
                is_window_days=252, oos_window_days=63, step_days=0,
            )
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
