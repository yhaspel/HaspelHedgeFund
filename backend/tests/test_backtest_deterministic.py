"""Deterministic (council-free) inverse-vol risk-parity engine path.

Covers inverse_vol_weights sizing math and run_deterministic_segment end-to-end
on seeded bars: long-only, gross-capped, never-negative equity.
"""
from __future__ import annotations

import datetime as dt
import math
import random
from decimal import Decimal

import pytest

from apps.backtests.engine import (
    DETERMINISTIC_DEFAULTS,
    inverse_vol_weights,
    rebalance_dates_for,
    run_deterministic_segment,
    trading_days,
)
from apps.data.models import DailyBar

pytestmark = pytest.mark.django_db

_VOLS = {"LOWV": 0.08, "MIDV": 0.16, "HIGHV": 0.32}  # annualised


def _fake_tr(ticker, as_of, lookback_days=60):
    d = _VOLS[ticker] / math.sqrt(252)
    return [d, -d] * 40


def _seed_bars(ticker, start, days, drift, vol, seed):
    rng = random.Random(seed)
    price = 100.0
    for i in range(days):
        d = start + dt.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        price = max(1.0, price * (1.0 + rng.gauss(drift, vol)))
        DailyBar.objects.create(
            ticker=ticker, date=d, source="fmp",
            open=Decimal(f"{price*0.999:.4f}"), high=Decimal(f"{price*1.01:.4f}"),
            low=Decimal(f"{price*0.99:.4f}"), close=Decimal(f"{price:.4f}"),
            adjusted_close=Decimal(f"{price:.4f}"), volume=1_000_000,
        )


class _StubBT:
    def __init__(self, universe, rebalance="monthly"):
        self.universe = universe
        self.starting_cash = Decimal("100000")
        self.commission_bps = Decimal("5")
        self.spread_bps = Decimal("5")
        self.rebalance_frequency = rebalance


def test_inverse_vol_weights_are_inverse_proportional(monkeypatch):
    from apps.backtests import engine
    monkeypatch.setattr(engine, "trailing_returns_for", _fake_tr)
    # cap=1.0 (no per-leg cap) + high vol_target (no down-scale) => pure inverse-vol
    cfg = {**DETERMINISTIC_DEFAULTS, "max_leg_weight": 1.0, "vol_target_annual": 0.50}
    w = inverse_vol_weights(day=dt.date(2024, 1, 2), universe=list(_VOLS), config=cfg)
    assert w["LOWV"] > w["MIDV"] > w["HIGHV"]            # lower vol -> larger weight
    assert w["LOWV"] / w["HIGHV"] == pytest.approx(4.0, rel=0.1)  # 1/.08 : 1/.32
    assert sum(w.values()) == pytest.approx(1.0, rel=0.02)        # fully invested
    # risk-parity property: each leg contributes equal risk (w_i * sigma_i equal)
    rc = [w[t] * _VOLS[t] for t in _VOLS]
    assert max(rc) - min(rc) < 1e-6


def test_book_is_vol_targeted_down_only(monkeypatch):
    from apps.backtests import engine
    monkeypatch.setattr(engine, "trailing_returns_for", _fake_tr)
    cfg = {**DETERMINISTIC_DEFAULTS, "max_leg_weight": 1.0, "vol_target_annual": 0.05}
    w = inverse_vol_weights(day=dt.date(2024, 1, 2), universe=list(_VOLS), config=cfg)
    # port vol of the inverse-vol book ~0.137 here; targeting 0.05 scales gross down
    assert sum(w.values()) < 0.5
    assert sum(w.values()) > 0.0


def test_no_history_returns_empty(monkeypatch):
    from apps.backtests import engine
    monkeypatch.setattr(engine, "trailing_returns_for", lambda *a, **k: [])
    out = inverse_vol_weights(
        day=dt.date(2024, 1, 2), universe=["X"], config=DETERMINISTIC_DEFAULTS
    )
    assert out == {}


def test_run_deterministic_segment_long_only_gross_capped_positive():
    start = dt.date(2023, 1, 1)
    for i, (tk, vol) in enumerate([("AAA", 0.006), ("BBB", 0.012), ("CCC", 0.020)]):
        _seed_bars(tk, start, 260, drift=0.0004, vol=vol, seed=i + 1)
    end = start + dt.timedelta(days=260)
    bt = _StubBT(["AAA", "BBB", "CCC"])
    days = trading_days(start, end, bt.universe)
    reb = rebalance_dates_for(days, "monthly")
    seg = run_deterministic_segment(
        bt=bt, start=start, end=end, config=DETERMINISTIC_DEFAULTS, rebalance_dates=reb
    )
    assert len(seg.equity) > 100
    assert all(e > 0 for e in seg.equity)                 # never negative
    assert any(snap for snap in seg.positions_by_day)     # it actually traded
    for snap in seg.positions_by_day:
        for p in snap:
            assert p["qty"] >= 0                          # long-only
    for snap, eq in zip(seg.positions_by_day, seg.equity, strict=True):
        gross = sum(abs(p["market_value"]) for p in snap)
        assert gross <= eq * 1.02 + 1.0                   # gross within ~100%


def test_carried_pf_avoids_rebuild_turnover():
    # Carrying the portfolio across contiguous segments must NOT re-establish the
    # book from cash — that fold-boundary rebuild was ~88% of bt33's turnover.
    from apps.backtests.portfolio import SimulatedPortfolio

    start = dt.date(2023, 1, 1)
    for i, (tk, vol) in enumerate([("AAA", 0.006), ("BBB", 0.012), ("CCC", 0.020)]):
        _seed_bars(tk, start, 260, drift=0.0004, vol=vol, seed=i + 1)
    bt = _StubBT(["AAA", "BBB", "CCC"])
    mid, end = start + dt.timedelta(days=130), start + dt.timedelta(days=260)

    def _run(s, e, pf):
        d = trading_days(s, e, bt.universe)
        r = rebalance_dates_for(d, "monthly")
        return run_deterministic_segment(
            bt=bt, start=s, end=e, config=DETERMINISTIC_DEFAULTS, rebalance_dates=r, pf=pf
        )

    def _max_day_notional(seg):
        return max(
            (sum(abs(fl.get("notional", 0.0)) for fl in day) for day in seg.fills_by_day),
            default=0.0,
        )

    pf = SimulatedPortfolio(starting_cash=100_000.0, commission_bps=5.0, spread_bps=5.0)
    _run(start, mid, pf)                                   # establish the book
    s2_carried = _run(mid + dt.timedelta(days=1), end, pf)        # carried forward
    s2_fresh = _run(mid + dt.timedelta(days=1), end, None)        # rebuilt from cash

    # fresh rebuilds the whole (vol-targeted) book from cash; carried only adjusts
    assert _max_day_notional(s2_fresh) > 40_000
    assert _max_day_notional(s2_carried) < 0.25 * _max_day_notional(s2_fresh)


def test_inverse_vol_weights_lever_to_max_gross(monkeypatch):
    from apps.backtests import engine
    monkeypatch.setattr(engine, "trailing_returns_for", _fake_tr)
    # high vol_target + max_gross=2.0 → book levers above 100% gross to hit the target
    cfg = {
        **DETERMINISTIC_DEFAULTS,
        "max_leg_weight": 1.0, "vol_target_annual": 0.20, "max_gross": 2.0,
    }
    w = inverse_vol_weights(day=dt.date(2024, 1, 2), universe=list(_VOLS), config=cfg)
    assert 1.0 < sum(w.values()) <= 2.0                  # levered, within the cap
    # same book unlevered stays ≤ 100%
    cfg0 = {**cfg, "max_gross": 1.0}
    w0 = inverse_vol_weights(day=dt.date(2024, 1, 2), universe=list(_VOLS), config=cfg0)
    assert sum(w0.values()) == pytest.approx(1.0, rel=0.02)


def test_deterministic_segment_levers_when_enabled():
    start = dt.date(2023, 1, 1)
    for i, (tk, vol) in enumerate([("AAA", 0.006), ("BBB", 0.012), ("CCC", 0.020)]):
        _seed_bars(tk, start, 260, drift=0.0004, vol=vol, seed=i + 1)
    end = start + dt.timedelta(days=260)
    bt = _StubBT(["AAA", "BBB", "CCC"])
    days = trading_days(start, end, bt.universe)
    reb = rebalance_dates_for(days, "monthly")
    cfg = {**DETERMINISTIC_DEFAULTS, "vol_target_annual": 0.30, "max_gross": 2.0}
    seg = run_deterministic_segment(
        bt=bt, start=start, end=end, config=cfg, rebalance_dates=reb
    )
    peak_gross = max(
        (sum(abs(p["market_value"]) for p in snap) / eq
         for snap, eq in zip(seg.positions_by_day, seg.equity, strict=True) if eq > 0),
        default=0.0,
    )
    assert peak_gross > 1.05                              # actually levered above 100%
    assert all(e > 0 for e in seg.equity)                # didn't blow up
