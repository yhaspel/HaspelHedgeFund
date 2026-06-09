"""P7c Part D — risk-parity leverage (deploy-faithful) + the deterministic
SPY-200dMA regime gate."""
from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from apps.backtests.engine import DETERMINISTIC_DEFAULTS, inverse_vol_weights, spy_regime_scale
from apps.backtests.serializers import BacktestCreateSerializer
from apps.data.models import DailyBar
from apps.portfolios.models import Portfolio, PortfolioStrategy, Universe, UniverseMembership

User = get_user_model()
pytestmark = pytest.mark.django_db
DAY = dt.date(2026, 1, 5)


def _seed_spy(prices: list[float]) -> None:
    start = DAY - dt.timedelta(days=len(prices))
    for i, p in enumerate(prices):
        DailyBar.objects.create(
            ticker="SPY", date=start + dt.timedelta(days=i),
            open=p, high=p, low=p, close=p, adjusted_close=p, volume=1, source="fmp",
        )


def test_regime_gate_risk_on_off_and_insufficient_history():
    # Rising 200 bars → latest above the 200-day MA → risk-on (1.0).
    _seed_spy([100.0 + i for i in range(200)])
    assert spy_regime_scale(DAY, floor=0.5) == 1.0
    DailyBar.objects.filter(ticker="SPY").delete()
    # Rise then crash below the MA → risk-off (floor).
    _seed_spy([100.0 + i for i in range(150)] + [250.0 - 3.0 * i for i in range(50)])
    assert spy_regime_scale(DAY, floor=0.4) == 0.4
    DailyBar.objects.filter(ticker="SPY").delete()
    # < window bars → fail-safe to full exposure.
    _seed_spy([100.0] * 50)
    assert spy_regime_scale(DAY, floor=0.5) == 1.0


_VOLS = {"A": 0.10, "B": 0.20, "C": 0.40}  # annual


def _fake_tr(ticker, as_of=None, lookback_days=60, price_field="close"):
    d = _VOLS[ticker] / math.sqrt(252)
    return [d, -d] * 40


def test_rp_leverage_deploy_faithful_branch(monkeypatch):
    from apps.backtests import engine
    monkeypatch.setattr(engine, "trailing_returns_for", _fake_tr)
    base = {**DETERMINISTIC_DEFAULTS, "sizing": "construct_risk_parity", "target_gross": 1.0}

    unlev = inverse_vol_weights(day=DAY, universe=list(_VOLS), config=base)
    assert sum(unlev.values()) == pytest.approx(1.0, rel=0.02)        # unlevered baseline

    # port_vol of the equal-risk book ≈ 0.171 → vt=0.40 levers ~2.3x; vt=0.10 de-grosses.
    lev = inverse_vol_weights(
        day=DAY, universe=list(_VOLS),
        config={**base, "rp_vol_target_annual": 0.40, "max_gross": 3.0},
    )
    assert sum(lev.values()) > 1.5                                    # levered up toward vt
    assert lev["A"] / lev["B"] == pytest.approx(unlev["A"] / unlev["B"])  # ratios preserved

    degross = inverse_vol_weights(
        day=DAY, universe=list(_VOLS),
        config={**base, "rp_vol_target_annual": 0.10, "max_gross": 3.0},
    )
    assert sum(degross.values()) < 0.8                               # de-grossed (vt < port_vol)

    capped = inverse_vol_weights(
        day=DAY, universe=list(_VOLS),
        config={**base, "rp_vol_target_annual": 5.0, "max_gross": 2.0},
    )
    assert sum(capped.values()) == pytest.approx(2.0, rel=0.02)      # leverage capped at max_gross


class _Req:
    def __init__(self, user):
        self.user = user


def _strategy(user, kind, **kw):
    u = Universe.objects.create(name=f"{kind}-{user.id}")
    for t in ["SPY", "TLT", "GLD"]:
        UniverseMembership.objects.create(universe=u, ticker=t, effective_from=dt.date(2020, 1, 1))
    pf = Portfolio.objects.create(
        user=user, kind=Portfolio.KIND_STRATEGY, name="pd", cash_balance=0
    )
    return PortfolioStrategy.objects.create(
        user=user, name="pd", kind=kind, universe=u, portfolio=pf, personas=[], **kw
    )


def test_serializer_threads_part_d_config():
    user = User.objects.create_user(email="pd@x.test", password="pw-fake-12345")
    s = _strategy(
        user, PortfolioStrategy.KIND_RISK_PARITY,
        rp_vol_target_annual=Decimal("0.30"), rp_max_gross=Decimal("3.00"),
        enable_spy_regime_gate=True, regime_gate_floor=Decimal("0.500"),
    )
    data = {
        "name": "v", "universe": ["SPY", "TLT", "GLD"], "start_date": "2010-06-01",
        "end_date": "2025-12-31", "is_window_days": 126, "oos_window_days": 63, "strategy_id": s.id,
    }
    ser = BacktestCreateSerializer(data=data, context={"request": _Req(user)})
    assert ser.is_valid(), ser.errors
    ss = ser.validated_data["search_space"]
    assert ser.validated_data["engine_mode"] == "risk_parity"
    assert ss["sizing"] == "construct_risk_parity"
    assert ss["rp_vol_target_annual"] == 0.30 and ss["max_gross"] == 3.0
    assert ss["enable_spy_regime_gate"] is True and ss["regime_gate_floor"] == 0.5
    # Defaults (off) for a plain strategy.
    u2 = User.objects.create_user(email="pd2@x.test", password="pw-fake-12345")
    s2 = _strategy(u2, PortfolioStrategy.KIND_TREND)
    ser2 = BacktestCreateSerializer(
        data={**data, "strategy_id": s2.id}, context={"request": _Req(u2)}
    )
    assert ser2.is_valid(), ser2.errors
    assert ser2.validated_data["search_space"]["rp_vol_target_annual"] == 0.0
    assert ser2.validated_data["search_space"]["enable_spy_regime_gate"] is False
