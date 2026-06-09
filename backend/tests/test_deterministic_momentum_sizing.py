"""Unit tests for the deterministic momentum sizers (P7c Part A):
tsmom_weights / xsec_momentum_weights and the run_deterministic_segment dispatch.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.backtests.engine import (
    _SIZERS,
    rebalance_dates_for,
    run_deterministic_segment,
    trading_days,
    tsmom_weights,
    xsec_momentum_weights,
)
from apps.backtests.models import Backtest
from apps.data.models import DailyBar

START = dt.date(2024, 1, 1)
# Short lookbacks keep fixtures tiny; the logic is lookback-length-independent.
CFG = {"momentum_lookbacks": [5, 10, 20], "vol_lookback_days": 10,
       "vol_target_annual": 0.15, "max_gross": 1.0}


def _seed(ticker: str, adj: list[float], close: list[float] | None = None) -> dt.date:
    close = close if close is not None else adj
    for i, (a, c) in enumerate(zip(adj, close, strict=True)):
        DailyBar.objects.create(
            ticker=ticker, date=START + dt.timedelta(days=i),
            open=Decimal(str(c)), high=Decimal(str(c)), low=Decimal(str(c)),
            close=Decimal(str(c)), adjusted_close=Decimal(str(a)), volume=1, source="fmp",
        )
    return START + dt.timedelta(days=len(adj))  # day strictly after the last bar


def _rising(n: int, base: float = 100.0, step: float = 1.0) -> list[float]:
    return [base + step * i for i in range(n)]


def _falling(n: int, base: float = 130.0, step: float = 1.0) -> list[float]:
    return [base - step * i for i in range(n)]


@pytest.mark.django_db
def test_tsmom_long_flat_holds_uptrend_drops_downtrend() -> None:
    day = _seed("UP", _rising(30))
    _seed("DOWN", _falling(30))
    w = tsmom_weights(day=day, universe=["UP", "DOWN"], config=CFG)
    assert w.get("UP", 0.0) > 0.0          # uptrend → long
    assert "DOWN" not in w                  # downtrend → flat (long/flat default)


@pytest.mark.django_db
def test_tsmom_allow_short_shorts_downtrend() -> None:
    day = _seed("UP", _rising(30))
    _seed("DOWN", _falling(30))
    w = tsmom_weights(day=day, universe=["UP", "DOWN"], config={**CFG, "allow_short": True})
    assert w["UP"] > 0.0
    assert w["DOWN"] < 0.0                  # downtrend → short when allowed


@pytest.mark.django_db
def test_tsmom_uses_total_return_adjusted_close() -> None:
    # Price (close) FLAT but adjusted_close rising (a dividend payer): total-return
    # momentum is positive even though price momentum is zero.
    flat = [100.0] * 30
    day = _seed("DIVPAYER", adj=_rising(30), close=flat)
    w_tr = tsmom_weights(day=day, universe=["DIVPAYER"], config=CFG)  # default adjusted_close
    assert w_tr.get("DIVPAYER", 0.0) > 0.0
    w_px = tsmom_weights(day=day, universe=["DIVPAYER"], config={**CFG, "price_field": "close"})
    assert "DIVPAYER" not in w_px           # flat price → no trend


@pytest.mark.django_db
def test_tsmom_vol_target_scales_gross() -> None:
    day = _seed("UP", _rising(40))
    uni = ["UP"]
    low = tsmom_weights(
        day=day, universe=uni, config={**CFG, "vol_target_annual": 0.05, "max_gross": 3.0}
    )
    high = tsmom_weights(
        day=day, universe=uni, config={**CFG, "vol_target_annual": 0.20, "max_gross": 3.0}
    )
    assert sum(abs(v) for v in high.values()) > sum(abs(v) for v in low.values())


@pytest.mark.django_db
def test_xsec_momentum_picks_top_n_with_positive_gate() -> None:
    # Three uptrends of decreasing strength + one downtrend.
    day = _seed("HOT", _rising(30, step=3.0))
    _seed("WARM", _rising(30, step=2.0))
    _seed("MILD", _rising(30, step=1.0))
    _seed("COLD", _falling(30))
    w = xsec_momentum_weights(day=day, universe=["HOT", "WARM", "MILD", "COLD"],
                              config={**CFG, "top_n": 2})
    assert set(w) == {"HOT", "WARM"}        # top-2 by momentum
    assert "COLD" not in w                  # momentum>0 gate excludes the downtrend
    assert all(v > 0 for v in w.values())   # long-only
    assert sum(w.values()) > 0              # deployed (vol-target-scaled gross)


@pytest.mark.django_db
def test_empty_when_no_history() -> None:
    day = _seed("SHORTHIST", _rising(3))    # < 20 returns
    assert tsmom_weights(day=day, universe=["SHORTHIST"], config=CFG) == {}
    assert xsec_momentum_weights(day=day, universe=["SHORTHIST"], config=CFG) == {}


@pytest.mark.django_db
def test_sizer_dispatch_registered() -> None:
    assert _SIZERS["tsmom"] is tsmom_weights
    assert _SIZERS["xsec_momentum"] is xsec_momentum_weights
    assert "trend" in Backtest.DETERMINISTIC_ENGINE_MODES
    assert "sector_momentum" in Backtest.DETERMINISTIC_ENGINE_MODES


@pytest.mark.django_db
def test_run_deterministic_segment_trend_holds_uptrend() -> None:
    _seed("UP", _rising(45))
    _seed("DOWN", _falling(45))
    universe = ["UP", "DOWN"]
    start, end = START, START + dt.timedelta(days=44)
    days = trading_days(start, end, universe)
    reb = rebalance_dates_for(days, "weekly")
    bt = SimpleNamespace(universe=universe, starting_cash=100000.0,
                         commission_bps=1.0, spread_bps=2.0, hold_semantics="hold_existing")
    cfg = {**CFG, "sizing": "tsmom"}
    seg = run_deterministic_segment(bt=bt, start=start, end=end, config=cfg, rebalance_dates=reb)
    final = seg.positions_by_day[-1]
    qty = {p["ticker"]: p["qty"] for p in final}
    assert qty.get("UP", 0) > 0             # trend went long the uptrend
    assert qty.get("DOWN", 0) <= 0          # never long the downtrend
    assert len(seg.equity) == len(days)
