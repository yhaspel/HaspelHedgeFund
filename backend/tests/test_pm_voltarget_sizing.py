"""Vol-target (inverse-vol) sizing must not collapse to equal-weight-at-cap.

The backtest optimizer searches max_weight in [0.03,0.15]. Clamping the
inverse-vol weight (vol_target/realized_vol, typically 0.3-5x) to that cap pins
every leg to the same value, defeating risk parity. The vol_target path must use
the separate vol_target_max_weight cap; the engine's gross cap normalizes Σ|w|.
"""
from __future__ import annotations

import math

import pytest

from hedgefund_agents.portfolio.portfolio_manager import (
    compute_target_weight,
    realized_vol_annual,
)


def _rets_for_vol(annual_vol: float, n: int = 80) -> list[float]:
    d = annual_vol / math.sqrt(252)
    return [d, -d] * (n // 2)


def test_realized_vol_helper_matches_target():
    assert realized_vol_annual(_rets_for_vol(0.30)) == pytest.approx(0.30, abs=0.02)


def test_vol_target_sizing_is_inverse_vol_not_clipped_to_max_weight():
    # Small searched max_weight (0.10) must NOT cap the inverse-vol weight.
    cfg = {
        "max_weight": 0.10,
        "vol_floor": 0.02,
        "vol_target_annual": 0.10,
        "vol_target_max_weight": 0.40,
    }
    w_lowvol = compute_target_weight(0.5, "buy", 80, 0.0, cfg, _rets_for_vol(0.20))
    w_highvol = compute_target_weight(0.5, "buy", 80, 0.0, cfg, _rets_for_vol(0.50))
    # vol 0.20 -> raw 0.50 -> capped at vt_cap 0.40; vol 0.50 -> raw 0.20 (uncapped)
    assert w_highvol == pytest.approx(0.20, abs=0.03)
    assert w_lowvol > w_highvol                 # inverse-vol ordering preserved
    assert w_highvol > cfg["max_weight"]        # the bug: old code clipped to 0.10


def test_vol_target_respects_vt_cap_ceiling():
    cfg = {
        "max_weight": 0.10,
        "vol_floor": 0.02,
        "vol_target_annual": 0.10,
        "vol_target_max_weight": 0.40,
    }
    # very low vol -> raw huge -> must clamp at vt_cap 0.40, not run away
    w = compute_target_weight(0.5, "buy", 80, 0.0, cfg, _rets_for_vol(0.05))
    assert w == pytest.approx(0.40, abs=1e-6)


def test_short_direction_inverse_vol_is_negative():
    cfg = {
        "max_weight": 0.10,
        "vol_floor": 0.02,
        "vol_target_annual": 0.10,
        "vol_target_max_weight": 0.40,
    }
    w = compute_target_weight(-0.5, "open_short", 80, 0.0, cfg, _rets_for_vol(0.50))
    assert w == pytest.approx(-0.20, abs=0.03)
