"""P7c Part B — deterministic momentum strategies (trend / sector_momentum) wired
to the deterministic engine + the autopilot→broker bridge. Verifies the live
constructors reuse the SAME engine sizers the backtest uses (live ≡ backtest),
the serializer auto-routes, the kinds are deterministic (so the ADR-0025 bridge
bypass applies), and the live cycle runs end-to-end to a DONE target.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from apps.backtests.engine import tsmom_weights, xsec_momentum_weights
from apps.backtests.serializers import BacktestCreateSerializer
from apps.data.models import DailyBar
from apps.portfolios.construction import (
    construct_sector_momentum,
    construct_trend,
    sector_momentum_config,
    trend_config,
)
from apps.portfolios.models import (
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    Universe,
    UniverseMembership,
)

User = get_user_model()
pytestmark = pytest.mark.django_db

AS_OF = dt.date(2024, 4, 1)


def _seed(ticker: str, n: int, base: float, step: float) -> None:
    """Seed `n` daily bars ending ON AS_OF (so the sizer sees n-1 trailing bars
    and the cycle's last_close finds the AS_OF bar)."""
    start = AS_OF - dt.timedelta(days=n - 1)
    for i in range(n):
        p = base + step * i
        DailyBar.objects.create(
            ticker=ticker, date=start + dt.timedelta(days=i),
            open=Decimal(str(p)), high=Decimal(str(p)), low=Decimal(str(p)),
            close=Decimal(str(p)), adjusted_close=Decimal(str(p)), volume=1, source="fmp",
        )


def _strategy(user, kind, **kw) -> PortfolioStrategy:
    u = Universe.objects.create(name=f"{kind}-{user.id}")
    for t, sec in [("UP", "Equity"), ("DOWN", "Rates")]:
        UniverseMembership.objects.create(
            universe=u, ticker=t, sector=sec, effective_from=dt.date(2020, 1, 1)
        )
    pf = Portfolio.objects.create(
        user=user, kind=Portfolio.KIND_STRATEGY, name=kind, cash_balance=Decimal("100000")
    )
    defaults = dict(
        kind=kind, target_gross_pct=Decimal("1.00"), vol_window_days=60, max_etfs_held=5,
        min_trade_notional_usd=Decimal("100"), max_turnover_pct=Decimal("5.0"), personas=[],
    )
    defaults.update(kw)
    return PortfolioStrategy.objects.create(
        user=user, name=kind, universe=u, portfolio=pf, **defaults
    )


class _Req:
    def __init__(self, user) -> None:
        self.user = user


def _base_data(strategy_id, **kw):
    d = {
        "name": "v", "universe": ["UP", "DOWN"], "start_date": "2008-06-01",
        "end_date": "2025-12-31", "is_window_days": 126, "oos_window_days": 63,
        "strategy_id": strategy_id,
    }
    d.update(kw)
    return d


def test_construct_trend_wraps_tsmom_weights():
    user = User.objects.create_user(email="m1@x.test", password="pw-fake-123456789")
    _seed("UP", 81, 100.0, 1.0)
    _seed("DOWN", 81, 180.0, -1.0)
    s = _strategy(user, PortfolioStrategy.KIND_TREND)
    members = [("UP", "Equity"), ("DOWN", "Rates")]
    cfg = trend_config(s)
    res = construct_trend(AS_OF, members, config=cfg)
    direct = tsmom_weights(day=AS_OF, universe=["UP", "DOWN"], config=cfg)
    # live ≡ backtest: the constructor wraps the SAME engine sizer.
    assert res.target_weights == {t: round(w, 6) for t, w in direct.items()}
    assert res.target_weights.get("UP", 0) > 0
    assert "DOWN" not in res.target_weights      # long/flat skips the downtrend


def test_construct_sector_momentum_wraps_xsec_weights():
    user = User.objects.create_user(email="m2@x.test", password="pw-fake-123456789")
    _seed("UP", 81, 100.0, 2.0)
    _seed("DOWN", 81, 180.0, -1.0)
    s = _strategy(user, PortfolioStrategy.KIND_SECTOR_MOMENTUM, max_etfs_held=1)
    members = [("UP", "Tech"), ("DOWN", "Energy")]
    cfg = sector_momentum_config(s)
    res = construct_sector_momentum(AS_OF, members, config=cfg)
    direct = xsec_momentum_weights(day=AS_OF, universe=["UP", "DOWN"], config=cfg)
    assert res.target_weights == {t: round(w, 6) for t, w in direct.items()}
    assert set(res.target_weights) == {"UP"}     # top_n=1 + momentum>0 gate


def test_config_builders_pin_canonical_params():
    user = User.objects.create_user(email="m3@x.test", password="pw-fake-123456789")
    s = _strategy(user, PortfolioStrategy.KIND_TREND, target_gross_pct=Decimal("2.00"))
    tc = trend_config(s)
    assert tc["sizing"] == "tsmom"
    assert tc["max_gross"] == 2.0                 # leverage from target_gross_pct
    assert tc["price_field"] == "adjusted_close"  # total-return momentum
    assert tc["momentum_lookbacks"] == [63, 126, 252]
    s2 = _strategy(user, PortfolioStrategy.KIND_SECTOR_MOMENTUM, max_etfs_held=4)
    sc = sector_momentum_config(s2)
    assert sc["sizing"] == "xsec_momentum" and sc["top_n"] == 4


def test_serializer_autoroutes_trend():
    user = User.objects.create_user(email="m4@x.test", password="pw-fake-123456789")
    s = _strategy(user, PortfolioStrategy.KIND_TREND)
    ser = BacktestCreateSerializer(data=_base_data(s.id), context={"request": _Req(user)})
    assert ser.is_valid(), ser.errors
    vd = ser.validated_data
    assert vd["engine_mode"] == "trend"
    assert vd["search_space"]["sizing"] == "tsmom"
    assert vd["search_space"]["price_field"] == "adjusted_close"


def test_serializer_autoroutes_sector_momentum():
    user = User.objects.create_user(email="m5@x.test", password="pw-fake-123456789")
    s = _strategy(user, PortfolioStrategy.KIND_SECTOR_MOMENTUM, max_etfs_held=3)
    ser = BacktestCreateSerializer(data=_base_data(s.id), context={"request": _Req(user)})
    assert ser.is_valid(), ser.errors
    vd = ser.validated_data
    assert vd["engine_mode"] == "sector_momentum"
    assert vd["search_space"]["sizing"] == "xsec_momentum"
    assert vd["search_space"]["top_n"] == 3


def test_momentum_kinds_are_deterministic():
    # Membership makes the ADR-0025 bridge bypass vol-target/equity-cap for them.
    assert PortfolioStrategy.KIND_TREND in PortfolioStrategy.DETERMINISTIC_KINDS
    assert PortfolioStrategy.KIND_SECTOR_MOMENTUM in PortfolioStrategy.DETERMINISTIC_KINDS


def test_run_momentum_cycle_trend_end_to_end(monkeypatch):
    from apps.portfolios import tasks

    user = User.objects.create_user(email="m6@x.test", password="pw-fake-123456789")
    _seed("UP", 81, 100.0, 1.0)
    _seed("DOWN", 81, 180.0, -1.0)
    s = _strategy(user, PortfolioStrategy.KIND_TREND)

    class _Stub:  # reads the seeded DailyBar; no real FMP
        def get_daily_bars(self, ticker, start, end, as_of):
            return list(
                DailyBar.objects.filter(
                    ticker=ticker, date__gte=start, date__lte=min(end, as_of)
                ).order_by("date")
            )

    monkeypatch.setattr(tasks, "get_fmp_provider", lambda **kw: _Stub())
    out = tasks._run_momentum_cycle(s, AS_OF, [("UP", "Equity"), ("DOWN", "Rates")], sizing="tsmom")

    assert out["status"] == "done"
    target = PortfolioTarget.objects.get(pk=out["target_id"])
    assert target.status == "done"
    assert target.target_weights.get("UP", 0) > 0
    assert "DOWN" not in target.target_weights
    assert out["orders"] >= 1               # at least a buy for UP
    # No broker link/autopilot → the bridge terminal hook is a clean no-op.
