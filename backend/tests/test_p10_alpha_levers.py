"""P10 §F — alpha-lever CODE (gating runs are deferred, by design).

F2: the ``xsec_long_short`` market-neutral sizer (top-N long / bottom-N short,
inverse-vol legs, beta-hedged) registered beside ``xsec_momentum``; negative
weights now actually SHORT through the deterministic engine (action
"open_short" — "sell" is target-0). F4: the per-pod SPY-200dMA regime-gate
validation path — a caller can pin ``enable_spy_regime_gate`` in a
strategy-linked backtest's search_space without editing the live config
(which would re-lock the §9 gate via the staleness rule).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from apps.backtests.engine import (
    _SIZERS,
    _trailing_beta,
    run_deterministic_segment,
    xsec_long_short_weights,
)
from apps.data.models import DailyBar
from apps.portfolios.models import (
    Portfolio,
    PortfolioStrategy,
    Universe,
    UniverseMembership,
)

User = get_user_model()

D0 = dt.date(2025, 1, 1)
N_DAYS = 110

# name -> (beta vs SPY, daily drift). Winners drift up, losers down; returns
# are EXACTLY beta×SPY + drift so trailing beta is exact.
NAMES = {
    "LNG1": (1.0, 0.004),
    "LNG2": (1.0, 0.003),
    "SHT1": (0.5, -0.004),
    "SHT2": (0.5, -0.003),
}

CONFIG = {
    "sizing": "xsec_long_short",
    "momentum_lookbacks": [20],
    "vol_lookback_days": 20,
    "top_n": 2,
    "bottom_n": 2,
    "beta_window_days": 80,
    "vol_target_annual": 10.0,   # never binds — isolates the leg math
    "max_leg_weight": 0.6,
}


@pytest.fixture
def bars(db):
    spy_rets = [0.01 if i % 2 == 0 else -0.008 for i in range(N_DAYS)]
    px = {"SPY": 100.0, **{t: 100.0 for t in NAMES}}
    for i in range(N_DAYS):
        d = D0 + dt.timedelta(days=i)
        if i:
            px["SPY"] *= 1 + spy_rets[i]
            for t, (beta, drift) in NAMES.items():
                px[t] *= 1 + beta * spy_rets[i] + drift
        for t, p in px.items():
            DailyBar.objects.create(
                ticker=t, date=d, open=p, high=p, low=p,
                close=Decimal(str(round(p, 6))),
                adjusted_close=Decimal(str(round(p, 6))),
                volume=1000, source="test",
            )
    return D0 + dt.timedelta(days=N_DAYS)  # the sizing day (strictly after bars)


def test_sizer_registered():
    assert _SIZERS["xsec_long_short"] is xsec_long_short_weights


# ---------------------------------------------------------------------------
# P11 F (R5) — single-name L/S pod SCAFFOLDING: KIND + construction wrapper +
# backtest routing exist for research/validation, but the pod is gate-blocked
# from live arming until a survivorship-clean single-name backfill (F1) lands.
# ---------------------------------------------------------------------------
def test_xsec_long_short_registered_as_scaffolding_kind():
    from apps.backtests.models import Backtest
    assert PortfolioStrategy.KIND_XSEC_LONG_SHORT in PortfolioStrategy.SCAFFOLDING_KINDS
    assert Backtest.XSEC_LONG_SHORT in Backtest.DETERMINISTIC_ENGINE_MODES
    # scaffolding is NOT treated as a live-deployable deterministic kind
    assert PortfolioStrategy.KIND_XSEC_LONG_SHORT not in PortfolioStrategy.DETERMINISTIC_KINDS


def test_construct_xsec_long_short_wraps_sizer(bars, db):
    from apps.portfolios.construction import construct_xsec_long_short
    members = [(t, "") for t in NAMES] + [("SPY", "")]
    res = construct_xsec_long_short(day=bars, members=members, config=CONFIG)
    w = res.target_weights
    assert w["LNG1"] > 0 and w["SHT1"] < 0          # long + short legs present
    assert res.gross_pct == pytest.approx(1.0, abs=1e-6)


def test_gate_blocks_arming_scaffolding_kind(db):
    from apps.portfolios.validation import validation_status
    user = User.objects.create_user(email="p11f@x.test", password="pw-fake-123456789")
    u = Universe.objects.create(name="p11f-uni")
    UniverseMembership.objects.create(universe=u, ticker="AAA", effective_from=D0)
    pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_STRATEGY, name="s")
    s = PortfolioStrategy.objects.create(
        user=user, name="single-name-ls", universe=u, portfolio=pf,
        kind=PortfolioStrategy.KIND_XSEC_LONG_SHORT,
    )
    status = validation_status(s)
    assert status["passed"] is False
    assert any(c["key"] == "deployable_kind" and not c["ok"] for c in status["checks"])


def test_serializer_routes_xsec_long_short_to_deterministic(db):
    from apps.backtests.models import Backtest
    from apps.backtests.serializers import BacktestCreateSerializer
    user = User.objects.create_user(email="p11f2@x.test", password="pw-fake-123456789")
    u = Universe.objects.create(name="p11f2-uni")
    UniverseMembership.objects.create(universe=u, ticker="AAA", effective_from=D0)
    pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_STRATEGY, name="s2")
    s = PortfolioStrategy.objects.create(
        user=user, name="ls2", universe=u, portfolio=pf,
        kind=PortfolioStrategy.KIND_XSEC_LONG_SHORT,
    )
    ser = BacktestCreateSerializer(
        data={"name": "ls-validation", "universe": ["AAA"],
              "start_date": "2023-01-02", "end_date": "2025-12-31", "strategy_id": s.id},
        context={"request": SimpleNamespace(user=user)},
    )
    assert ser.is_valid(), ser.errors
    assert ser.validated_data["engine_mode"] == Backtest.XSEC_LONG_SHORT
    assert ser.validated_data["search_space"]["sizing"] == "xsec_long_short"


def test_long_short_legs_and_market_neutrality(bars, db):
    day = bars
    w = xsec_long_short_weights(day=day, universe=[*NAMES, "SPY"], config=CONFIG)
    assert w, "sizer returned empty weights"
    assert w["LNG1"] > 0 and w["LNG2"] > 0
    assert w["SHT1"] < 0 and w["SHT2"] < 0
    gross = sum(abs(v) for v in w.values())
    assert gross == pytest.approx(1.0, abs=1e-6)
    # Beta-hedged: Σ w·β ≈ 0 (longs β=1, shorts β=0.5 → shorts scaled ~2×).
    betas = {t: _trailing_beta(t, day, window=80) for t in w}
    book_beta = sum(w[t] * betas[t] for t in w)
    assert abs(book_beta) < 0.05
    # Without the hedge the same book carries ~+0.25 of beta.
    w_raw = xsec_long_short_weights(
        day=day, universe=[*NAMES, "SPY"], config={**CONFIG, "beta_hedge": False},
    )
    raw_beta = sum(w_raw[t] * betas[t] for t in w_raw)
    assert raw_beta > 0.15


def test_long_short_shorts_actually_execute(bars, db):
    """Negative weights must open SHORT positions through the deterministic
    engine — the old decision mapping sent them to action 'sell' (= target 0),
    silently dropping every short."""
    day_start = bars
    # Extend bars a few more days so the segment has trading days to replay.
    px = {
        t: float(DailyBar.objects.filter(ticker=t).order_by("-date").first().close)
        for t in [*NAMES, "SPY"]
    }
    for i in range(5):
        d = day_start + dt.timedelta(days=i)
        for t, p in px.items():
            DailyBar.objects.create(
                ticker=t, date=d, open=p, high=p, low=p,
                close=Decimal(str(round(p, 6))),
                adjusted_close=Decimal(str(round(p, 6))),
                volume=1000, source="test",
            )
    bt = SimpleNamespace(
        starting_cash=100_000.0, commission_bps=0.0, spread_bps=0.0,
        universe=[*NAMES, "SPY"], hold_semantics="hold_existing",
    )
    seg = run_deterministic_segment(
        bt=bt, start=day_start, end=day_start + dt.timedelta(days=4), config=CONFIG,
    )
    final_positions = {p["ticker"]: p["qty"] for p in seg.positions_by_day[-1]}
    assert any(q < 0 for q in final_positions.values()), (
        f"no short positions opened: {final_positions}"
    )
    assert final_positions.get("SHT1", 0) < 0


# ---------------------------------------------------------------------------
# F4 — the regime-gate VALIDATION path: pin the gate in a strategy-linked
# backtest's search_space without editing the live strategy (whose updated_at
# bump would re-lock the §9 enable toggle).
# ---------------------------------------------------------------------------
def test_gated_validation_backtest_without_config_edit(db):
    from apps.backtests.serializers import BacktestCreateSerializer

    user = User.objects.create_user(email="p10f@x.test", password="pw-fake-123456789")
    u = Universe.objects.create(name="p10f-uni")
    UniverseMembership.objects.create(universe=u, ticker="AAA", effective_from=D0)
    pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_STRATEGY, name="s")
    s = PortfolioStrategy.objects.create(
        user=user, name="trend-pod", universe=u, portfolio=pf,
        kind=PortfolioStrategy.KIND_TREND,
        enable_spy_regime_gate=False,            # live config: gate OFF
    )
    before = s.updated_at
    ser = BacktestCreateSerializer(
        data={
            "name": "gated validation", "universe": ["AAA"],
            "start_date": "2023-01-02", "end_date": "2025-12-31",
            "strategy_id": s.id,
            # The caller pins the gate ON for THIS run only:
            "search_space": {"enable_spy_regime_gate": True, "regime_gate_floor": 0.5},
        },
        context={"request": SimpleNamespace(user=user)},
    )
    assert ser.is_valid(), ser.errors
    ss = ser.validated_data["search_space"]
    assert ss["enable_spy_regime_gate"] is True          # caller override wins
    assert ss["sizing"] == "tsmom"                       # still trend-faithful
    s.refresh_from_db()
    assert s.updated_at == before                        # live config untouched
    assert s.enable_spy_regime_gate is False
