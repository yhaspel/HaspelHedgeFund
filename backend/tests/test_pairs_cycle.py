"""P2k follow-ups: regime-break + atomic-close tests."""
from __future__ import annotations

import math
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from apps.portfolios.models import (
    Pair,
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    Position,
    RebalanceOrder,
    Universe,
    UniverseMembership,
)
from apps.portfolios.pairs import decide_open_pair_action

User = get_user_model()


# ---------- Regime-break (pure unit, no DB) -----------------------------

def _stationary_spread(n: int = 200) -> list[float]:
    """AR(1) with ρ=0.5 → strongly mean-reverting; passes the ADF gate."""
    import random
    rng = random.Random(7)
    x = [0.0]
    for _ in range(n - 1):
        x.append(0.5 * x[-1] + rng.gauss(0, 0.5))
    return x


def _drifting_spread(n: int = 200) -> list[float]:
    """Random walk → non-stationary; fails the ADF gate."""
    import random
    rng = random.Random(13)
    x = [0.0]
    for _ in range(n - 1):
        x.append(x[-1] + rng.gauss(0, 1.0))
    return x


def _build_log_prices_from_spread(
    spread: list[float], hedge_ratio: float = 1.0
) -> dict[str, list[float]]:
    """Build (la, lb) such that la - β·lb = spread, with lb a smooth random walk."""
    import random
    rng = random.Random(99)
    n = len(spread)
    lb = [math.log(50.0)]
    for _ in range(n - 1):
        lb.append(lb[-1] + rng.gauss(0, 0.005))
    la = [spread[i] + hedge_ratio * lb[i] for i in range(n)]
    return {"A": la, "B": lb}


def test_regime_break_force_closes_after_third_consecutive_failure():
    # A non-stationary spread → ADF-like p_value > p_max. We arrange the
    # current z to sit inside the action band (not exit, not stop) so the
    # only reason to close is regime-break.
    spread = _drifting_spread(200)
    # Force the last point to a moderate z (≈1.5) — between exit_z (0.5) and stop_z (4).
    mean = sum(spread) / len(spread)
    std = (sum((s - mean) ** 2 for s in spread) / (len(spread) - 1)) ** 0.5
    spread[-1] = mean + 1.5 * std  # z ≈ 1.5
    lp = _build_log_prices_from_spread(spread)

    # First two cycles: cointegration fails but counter not yet at threshold.
    for counter in (0, 1):
        action, z, p = decide_open_pair_action(
            leg_a="A", leg_b="B",
            hedge_ratio=1.0, spread_mean=mean, spread_std=std,
            log_prices=lp,
            exit_z=0.5, stop_z=4.0, p_max=0.05,
            consecutive_failures=counter, regime_break_after=3,
        )
        assert action == "hold", f"counter={counter} → expected hold, got {action} (p={p})"
        assert p is not None and p > 0.05

    # Third consecutive failure: force-close.
    action, z, p = decide_open_pair_action(
        leg_a="A", leg_b="B",
        hedge_ratio=1.0, spread_mean=mean, spread_std=std,
        log_prices=lp,
        exit_z=0.5, stop_z=4.0, p_max=0.05,
        consecutive_failures=2, regime_break_after=3,
    )
    assert action == "regime_break"


def test_stationary_spread_does_not_trigger_regime_break():
    spread = _stationary_spread(200)
    mean = sum(spread) / len(spread)
    std = (sum((s - mean) ** 2 for s in spread) / (len(spread) - 1)) ** 0.5
    # Pin to a mid-band z.
    spread[-1] = mean + 1.2 * std
    lp = _build_log_prices_from_spread(spread)
    action, z, p = decide_open_pair_action(
        leg_a="A", leg_b="B",
        hedge_ratio=1.0, spread_mean=mean, spread_std=std,
        log_prices=lp,
        exit_z=0.5, stop_z=4.0, p_max=0.20,
        consecutive_failures=2, regime_break_after=3,
    )
    # Stationary spread → ADF passes → no regime break even with counter near limit.
    assert action == "hold"
    assert p is not None and p < 0.20


def test_exit_inside_threshold_returns_reverted_not_regime_break():
    # Even if the cointegration test would fail, a z inside the exit band
    # closes as 'reverted' — exit/stop checks come BEFORE the regime check.
    spread = _drifting_spread(200)
    mean = sum(spread) / len(spread)
    std = (sum((s - mean) ** 2 for s in spread) / (len(spread) - 1)) ** 0.5
    spread[-1] = mean + 0.1 * std  # |z| ≈ 0.1 < exit_z
    lp = _build_log_prices_from_spread(spread)
    action, z, p = decide_open_pair_action(
        leg_a="A", leg_b="B",
        hedge_ratio=1.0, spread_mean=mean, spread_std=std,
        log_prices=lp,
        exit_z=0.5, stop_z=4.0, p_max=0.05,
        consecutive_failures=99, regime_break_after=3,
    )
    assert action == "reverted"


# ---------- Atomic close (DB integration) -------------------------------

class _FakeBar:
    def __init__(self, close: float) -> None:
        self.close = close
        self.adjusted_close = close


class _FakeDataProvider:
    """Returns synthetic log-price paths shaped so a target Pair will revert.

    We give A and B nearly-identical trajectories with a fixed offset and a
    tiny stationary spread. With hedge_ratio=1.0 the current spread sits at
    the mean → z ≈ 0 → 'reverted'.
    """

    def __init__(self) -> None:
        self.calls = 0

    def get_daily_bars(self, ticker, start, end, *, as_of):
        self.calls += 1
        # 260 trading days of slight drift; both legs share the same path so
        # spread stays near zero.
        bars = []
        base = 4.0
        for i in range(260):
            bars.append(_FakeBar(close=math.exp(base + i * 0.0005)))
        return bars


@pytest.fixture
def pairs_strategy(db):
    user = User.objects.create_user(email="pairs@test.com", password="x" * 12)
    uni = Universe.objects.create(name="p-uni", description="d")
    UniverseMembership.objects.create(universe=uni, ticker="A", sector="X",
                                      effective_from=date(2020, 1, 1))
    UniverseMembership.objects.create(universe=uni, ticker="B", sector="X",
                                      effective_from=date(2020, 1, 1))
    pf = Portfolio.objects.create(
        user=user, name="book", cash_balance=Decimal("100000"),
    )
    strategy = PortfolioStrategy.objects.create(
        user=user, name="Atomic-close test", kind=PortfolioStrategy.KIND_PAIRS,
        universe=uni, portfolio=pf,
        pair_entry_z=Decimal("2.0"), pair_exit_z=Decimal("0.5"),
        pair_stop_z=Decimal("4.0"), pair_max_held=2,
        pair_notional_pct=Decimal("0.05"),
        pair_cointegration_p_max=Decimal("0.50"),
        pair_lookback_days=120,
        pair_correlation_min=Decimal("0.20"),
        enable_pair_council=False,
    )
    return strategy, pf


def test_atomic_close_emits_paired_sequence_zero_orders(monkeypatch, pairs_strategy):
    """Open pair whose current z is within exit threshold must close both legs
    atomically: two RebalanceOrders, both sequence=0, both pointing at the
    same Pair via the .pair FK."""
    strategy, pf = pairs_strategy
    # Seed an open Pair with stats chosen so z ≈ 0 on _FakeDataProvider.
    pair = Pair.objects.create(
        strategy=strategy,
        leg_a_ticker="A", leg_b_ticker="B", sector="X",
        cointegration_p_value=0.01, correlation=0.95,
        hedge_ratio=1.0, spread_mean=0.0, spread_std=0.1,
        spread_window_days=120,
        entry_date=date(2024, 11, 1), entry_z=2.4,
        status="open",
    )
    # Existing positions on both legs (long A, short B).
    Position.objects.create(portfolio=pf, ticker="A", quantity=Decimal("50"),
                            avg_cost=Decimal("55"), sector="X")
    Position.objects.create(portfolio=pf, ticker="B", quantity=Decimal("-50"),
                            avg_cost=Decimal("55"), sector="X")

    # Stub the provider used by tasks.py.
    from hedgefund_agents import registry as agent_registry
    monkeypatch.setattr(agent_registry, "get_data_provider", lambda: _FakeDataProvider())

    from apps.portfolios.tasks import _run_pairs_cycle
    from apps.portfolios.tasks import _active_members
    members = _active_members(strategy, date(2024, 12, 2))
    res = _run_pairs_cycle(strategy, date(2024, 12, 2), members)

    pair.refresh_from_db()
    assert pair.status == "closed", f"pair still {pair.status}"
    assert pair.exit_reason == "reverted"

    target = PortfolioTarget.objects.get(pk=res["target_id"])
    closes = RebalanceOrder.objects.filter(target=target, sequence=0)
    tickers = sorted(o.ticker for o in closes)
    assert tickers == ["A", "B"], f"expected both legs closed atomically, got {tickers}"
    for o in closes:
        # pair FK is best-effort attached by the cycle when the position is
        # tied to the closing pair; both legs should map back to our Pair.
        assert o.pair_id == pair.pk, f"order on {o.ticker} not linked to pair"
        assert o.reason == "close"
