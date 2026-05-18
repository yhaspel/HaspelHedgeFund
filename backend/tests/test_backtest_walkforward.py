"""Walk-forward orchestration: fold generation, segment determinism,
cache-reuse zero-LLM-call assertion, leakage property, deflation."""
from __future__ import annotations

import datetime as dt
import random
from decimal import Decimal

import pytest

from apps.backtests import cache as bt_cache
from apps.backtests.engine import rebalance_dates_for, run_segment, trading_days
from apps.backtests.optimizer import optimize_is, sample_candidate
from apps.backtests.walkforward import generate_folds, run_walkforward
from apps.data.models import DailyBar


pytestmark = pytest.mark.django_db


# ---------- helpers ----------

def _seed_bars(ticker: str, start: dt.date, days: int, drift: float = 0.0005, vol: float = 0.012,
               seed: int = 1) -> None:
    rng = random.Random(seed)
    price = 100.0
    for i in range(days):
        d = start + dt.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        # Geometric Brownian motion-ish path
        r = rng.gauss(drift, vol)
        price = max(1.0, price * (1.0 + r))
        DailyBar.objects.create(
            ticker=ticker, date=d, source="fmp",
            open=Decimal(f"{price * 0.999:.4f}"),
            high=Decimal(f"{price * 1.01:.4f}"),
            low=Decimal(f"{price * 0.99:.4f}"),
            close=Decimal(f"{price:.4f}"),
            adjusted_close=Decimal(f"{price:.4f}"),
            volume=1_000_000,
        )


class _StubBT:
    """Minimal stand-in for Backtest model for unit-level tests."""
    def __init__(self, universe, start, end, **kw):
        self.id = 0
        self.universe = universe
        self.start_date = start
        self.end_date = end
        self.starting_cash = Decimal("100000")
        self.commission_bps = Decimal("5")
        self.spread_bps = Decimal("5")
        self.rebalance_frequency = kw.get("rebalance_frequency", "weekly")
        self.search_space = {}
        self.is_objective = "sharpe"
        self.rng_seed = 42
        self.n_candidates = 5


def _fake_persona_outputs(ticker: str, day: dt.date, bullish_strength: float = 0.7) -> dict:
    """Return persona outputs that depend ONLY on (ticker, day) so a re-prime
    yields identical content. bullish_strength shifts the signal distribution."""
    h = hash((ticker, day.toordinal()))
    signal = "bullish" if (h % 10) / 10.0 < bullish_strength else "bearish"
    return {
        "buffett": {"signal": signal, "confidence": 75, "thesis": ""},
        "munger": {"signal": signal, "confidence": 70, "thesis": ""},
        "graham": {"signal": signal, "confidence": 80, "thesis": ""},
        "wood": {"signal": signal, "confidence": 65, "thesis": ""},
        "druckenmiller": {"signal": signal, "confidence": 60, "thesis": ""},
        "burry": {"signal": "neutral", "confidence": 50, "thesis": ""},
        "damodaran": {"signal": signal, "confidence": 70, "thesis": ""},
        "lynch": {"signal": signal, "confidence": 65, "thesis": ""},
        "risk": {"veto": False, "max_position_pct_for_this_trade": 0.10, "hard_caps_applied": []},
        "valuation": {"current_price": 100.0},
    }


def _build_cache(universe, days):
    return {
        (t, d): _fake_persona_outputs(t, d) for t in universe for d in days
    }


# ---------- tests ----------

def test_generate_folds_nonoverlapping():
    folds = generate_folds(
        start=dt.date(2024, 1, 1), end=dt.date(2025, 12, 31),
        is_window_days=252, oos_window_days=63, step_days=63,
    )
    assert len(folds) >= 3
    # OOS ranges must be non-overlapping (step >= oos_window)
    for a, b in zip(folds, folds[1:]):
        assert b.oos_start > a.oos_end
    # IS must precede OOS for every fold
    for f in folds:
        assert f.is_end < f.oos_start


def test_rebalance_dates_weekly_vs_daily():
    days = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(20)]
    days = [d for d in days if d.weekday() < 5]
    assert rebalance_dates_for(days, "daily") == set(days)
    weekly = rebalance_dates_for(days, "weekly")
    # Should be one per ISO week
    assert len(weekly) == len({d.isocalendar()[:2] for d in days})


def test_segment_is_deterministic_with_same_seed_and_cache():
    _seed_bars("AAA", dt.date(2024, 1, 1), 90, seed=1)
    _seed_bars("BBB", dt.date(2024, 1, 1), 90, seed=2)
    bt = _StubBT(["AAA", "BBB"], dt.date(2024, 2, 1), dt.date(2024, 3, 28))
    days = trading_days(bt.start_date, bt.end_date, bt.universe)
    cache = _build_cache(bt.universe, days)
    pm = {"buy_threshold": 0.2, "sell_threshold": -0.2, "max_weight": 0.1,
          "vol_target_annual": None, "weights": None}
    s1 = run_segment(bt=bt, start=bt.start_date, end=bt.end_date,
                    pm_config=pm, agent_outputs_cache=cache,
                    rebalance_dates=set(days))
    s2 = run_segment(bt=bt, start=bt.start_date, end=bt.end_date,
                    pm_config=pm, agent_outputs_cache=cache,
                    rebalance_dates=set(days))
    assert s1.equity == s2.equity
    assert s1.cash == s2.cash


def test_optimizer_makes_no_llm_calls():
    """IS sweep against a primed cache must hit zero LLM calls."""
    bt_cache.reset_counters()
    _seed_bars("AAA", dt.date(2024, 1, 1), 200, seed=1)
    bt = _StubBT(["AAA"], dt.date(2024, 1, 15), dt.date(2024, 4, 30))
    bt.n_candidates = 5
    days = trading_days(bt.start_date, bt.end_date, bt.universe)
    cache = _build_cache(bt.universe, days)
    res = optimize_is(
        bt=bt, is_start=bt.start_date, is_end=bt.end_date,
        agent_outputs_cache=cache, personas=["buffett", "munger"],
        n_candidates=5,
    )
    assert len(res) == 5
    assert bt_cache.counter("misses") == 0  # no LLM-cache miss attempts at all


def test_fold_boundary_no_oos_leakage_into_is_winner():
    """Tampering with a row inside OOS must not change the IS winner."""
    _seed_bars("AAA", dt.date(2024, 1, 1), 300, seed=1)
    bt = _StubBT(["AAA"], dt.date(2024, 1, 15), dt.date(2024, 7, 31))
    is_start = dt.date(2024, 2, 1)
    is_end = dt.date(2024, 4, 30)
    days = trading_days(is_start, is_end, bt.universe)
    cache_a = _build_cache(bt.universe, days)
    # Add an OOS day in the cache; optimizer is told only about IS range
    oos_day = dt.date(2024, 5, 15)
    cache_a[("AAA", oos_day)] = _fake_persona_outputs("AAA", oos_day, bullish_strength=0.95)
    cache_b = dict(cache_a)
    # Tamper the OOS-only entry
    cache_b[("AAA", oos_day)] = _fake_persona_outputs("AAA", oos_day, bullish_strength=0.05)
    w_a = optimize_is(
        bt=bt, is_start=is_start, is_end=is_end,
        agent_outputs_cache=cache_a, personas=["buffett", "munger"], n_candidates=3,
    )[0]
    w_b = optimize_is(
        bt=bt, is_start=is_start, is_end=is_end,
        agent_outputs_cache=cache_b, personas=["buffett", "munger"], n_candidates=3,
    )[0]
    # OOS tamper should NOT affect IS winner score
    assert w_a.score == w_b.score
    assert w_a.config == w_b.config


def test_sample_candidate_weights_sum_to_one():
    rng = random.Random(0)
    c = sample_candidate(rng=rng, personas=["a", "b", "c"], search_space={})
    s = sum(c["weights"].values())
    assert abs(s - 1.0) < 1e-9
