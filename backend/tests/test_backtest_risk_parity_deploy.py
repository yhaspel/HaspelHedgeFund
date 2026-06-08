"""Risk-parity deploy bridge.

(1) The backtest's `sizing="construct_risk_parity"` mode reuses the live
    production constructor exactly, so a backtest faithfully models how a
    risk_parity strategy trades live.
(2) A risk_parity strategy's validation backtest auto-routes to the deterministic
    engine + that sizing (so the gate validates the real trading logic).
"""
from __future__ import annotations

import datetime as dt
import math
import statistics
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from apps.backtests.engine import DETERMINISTIC_DEFAULTS, inverse_vol_weights
from apps.backtests.serializers import BacktestCreateSerializer
from apps.portfolios.construction import construct_risk_parity
from apps.portfolios.models import Portfolio, PortfolioStrategy, Universe, UniverseMembership

User = get_user_model()
pytestmark = pytest.mark.django_db

_VOLS = {"A": 0.10, "B": 0.20, "C": 0.40}  # annual


def _fake_tr(ticker, as_of=None, lookback_days=60):
    d = _VOLS[ticker] / math.sqrt(252)
    return [d, -d] * 40


def test_faithful_mode_matches_construct_risk_parity(monkeypatch):
    from apps.backtests import engine
    monkeypatch.setattr(engine, "trailing_returns_for", _fake_tr)
    cfg = {**DETERMINISTIC_DEFAULTS, "sizing": "construct_risk_parity", "target_gross": 1.0}
    w = inverse_vol_weights(day=dt.date(2024, 1, 2), universe=list(_VOLS), config=cfg)

    dvols = {t: statistics.pstdev(_fake_tr(t)) for t in _VOLS}
    expected = construct_risk_parity(
        [(t, "") for t in dvols], dvols,
        target_gross_pct=1.0, per_sleeve_max_pct=0.50, per_sleeve_min_pct=0.02,
    ).target_weights

    assert w == pytest.approx(expected)                  # exact reuse of the live constructor
    assert w["A"] > w["B"] > w["C"]                       # inverse-vol ordering
    assert sum(w.values()) == pytest.approx(1.0, rel=0.02)  # 100% gross


def _rp_strategy(user):
    u = Universe.objects.create(name="rp-uni")
    for t in ["SPY", "TLT", "GLD"]:
        UniverseMembership.objects.create(universe=u, ticker=t, effective_from=dt.date(2020, 1, 1))
    pf = Portfolio.objects.create(
        user=user, kind=Portfolio.KIND_STRATEGY, name="rp", cash_balance=Decimal("100000")
    )
    return PortfolioStrategy.objects.create(
        user=user, name="rp", universe=u, portfolio=pf,
        kind=PortfolioStrategy.KIND_RISK_PARITY, personas=[],
    )


class _Req:
    def __init__(self, user):
        self.user = user


def _base_data(strategy_id, **kw):
    d = {
        "name": "v", "universe": ["SPY", "TLT", "GLD"],
        "start_date": "2008-06-01", "end_date": "2025-12-31",
        "is_window_days": 126, "oos_window_days": 63, "strategy_id": strategy_id,
    }
    d.update(kw)
    return d


def test_risk_parity_strategy_routes_to_deterministic_engine():
    user = User.objects.create_user(email="rp@x.test", password="pw-fake-123456789")
    s = _rp_strategy(user)
    ser = BacktestCreateSerializer(data=_base_data(s.id), context={"request": _Req(user)})
    assert ser.is_valid(), ser.errors
    vd = ser.validated_data
    assert vd["engine_mode"] == "risk_parity"
    assert vd["search_space"]["sizing"] == "construct_risk_parity"


def test_explicit_engine_mode_is_respected():
    user = User.objects.create_user(email="rp2@x.test", password="pw-fake-123456789")
    s = _rp_strategy(user)
    ser = BacktestCreateSerializer(
        data=_base_data(s.id, engine_mode="council"), context={"request": _Req(user)}
    )
    assert ser.is_valid(), ser.errors
    assert ser.validated_data["engine_mode"] == "council"  # caller's explicit choice wins
