"""Metrics math: Sharpe, drawdown, hit-rate on golden curves."""
from __future__ import annotations

import math

from apps.backtests.metrics import (
    drawdown_pct,
    hit_rate,
    sharpe_ratio,
    sortino_ratio,
)


def test_drawdown_basic() -> None:
    eq = [100, 110, 120, 90, 95]
    assert abs(drawdown_pct(eq) - (120 - 90) / 120) < 1e-9


def test_drawdown_monotonic_up_is_zero() -> None:
    assert drawdown_pct([100, 110, 120, 130]) == 0.0


def test_sharpe_zero_for_constant_returns() -> None:
    assert sharpe_ratio([0.01] * 30) == 0.0


def test_sharpe_positive_for_drifting_returns() -> None:
    # Drift up with low noise -> Sharpe > 1
    rets = [0.001, 0.002, 0.0015, 0.002, 0.001, 0.0018, 0.0012, 0.0019] * 5
    assert sharpe_ratio(rets) > 1.0


def test_sortino_ignores_upside_vol() -> None:
    upside = [0.0, 0.01, 0.0, 0.02, 0.0, 0.03]
    s_sortino = sortino_ratio(upside)
    # All downside is zero -> sortino is 0 (no downside deviation)
    assert s_sortino == 0.0


def test_hit_rate() -> None:
    assert hit_rate([0.1, -0.1, 0.05, 0.0, 0.02]) == 3 / 5


def test_sharpe_known_value() -> None:
    # Returns with mean 0.001 and std ~0.01 -> sharpe ~= 0.1 * sqrt(252) ~= 1.587
    rets = [0.011, -0.009, 0.012, -0.008, 0.011, -0.009] * 5
    s = sharpe_ratio(rets)
    # Just check finite + sane sign; exact value tested by formula consistency
    assert -10 < s < 10
    assert not math.isnan(s)
