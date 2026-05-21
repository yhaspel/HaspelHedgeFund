"""P2k follow-ups: regime-break + atomic-close tests."""
from __future__ import annotations

import math
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from apps.portfolios.models import (
    Pair,
    PairZHistory,
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


def test_borrow_unlocatable_short_leg_drops_entire_pair(monkeypatch, pairs_strategy):
    """If the short leg is on the HTB list, the whole pair must be rejected
    (long-only orphans are not allowed in pairs trading)."""
    strategy, pf = pairs_strategy
    # Make the universe include an HTB ticker so it can appear in a screen result.
    UniverseMembership.objects.create(
        universe=strategy.universe, ticker="GME", sector="X",
        effective_from=date(2020, 1, 1),
    )

    # Force the screener to return a single candidate with GME as leg_b.
    from apps.portfolios import pairs as pairs_mod
    from apps.portfolios import tasks
    fake_candidate = pairs_mod.PairCandidate(
        leg_a="A", leg_b="GME", sector="X",
        hedge_ratio=1.0, spread_mean=0.0, spread_std=0.1,
        z_current=-3.0, p_value=0.01, correlation=0.95,
        n_obs=200, synthetic=False,
    )
    monkeypatch.setattr(
        tasks, "screen_pairs",
        lambda *_a, **_kw: ([fake_candidate], {"n_pairs_evaluated": 1,
                                               "n_pairs_cointegrated": 1,
                                               "n_pairs_above_entry_z": 1,
                                               "synthetic_tickers": []}),
    )
    from apps.portfolios import tasks as tasks_mod
    monkeypatch.setattr(tasks_mod, "get_fmp_provider", lambda *a, **kw: _FakeDataProvider())

    members = tasks._active_members(strategy, date(2024, 12, 2))
    res = tasks._run_pairs_cycle(strategy, date(2024, 12, 2), members)

    # No Pair row was created for the vetoed pair.
    assert Pair.objects.filter(strategy=strategy, status="open").count() == 0
    # Borrow veto was logged in cycle diagnostics.
    target = PortfolioTarget.objects.get(pk=res["target_id"])
    veto_log = target.beta_diagnostics.get("borrow_vetoed_pairs") or []
    assert any(e["leg_a"] == "A" and e["leg_b"] == "GME" for e in veto_log), \
        f"expected GME borrow veto in diagnostics, got {veto_log}"


def test_z_history_accumulates_across_cycles(monkeypatch, pairs_strategy):
    """Each cycle that runs with an open pair writes one PairZHistory row;
    consecutive cycles accumulate; the cycle diagnostics expose a z_history slice."""
    strategy, pf = pairs_strategy
    # Seed an open pair (no closes — leg prices yield z mid-band so it just holds).
    pair = Pair.objects.create(
        strategy=strategy,
        leg_a_ticker="A", leg_b_ticker="B", sector="X",
        cointegration_p_value=0.01, correlation=0.95,
        hedge_ratio=1.0,
        spread_mean=-0.05, spread_std=0.05,    # synthetic stats designed so z ≈ -1.5
        spread_window_days=120,
        entry_date=date(2024, 11, 1), entry_z=2.4,
        status="open",
    )
    from apps.portfolios import tasks as tasks_mod
    monkeypatch.setattr(tasks_mod, "get_fmp_provider", lambda *a, **kw: _FakeDataProvider())
    # Skip new-candidate screening for speed.
    monkeypatch.setattr(
        tasks_mod, "screen_pairs",
        lambda *_a, **_kw: ([], {"n_pairs_evaluated": 0, "n_pairs_cointegrated": 0,
                                  "n_pairs_above_entry_z": 0, "synthetic_tickers": []}),
    )

    members = tasks_mod._active_members(strategy, date(2024, 12, 2))
    tasks_mod._run_pairs_cycle(strategy, date(2024, 12, 2), members)
    res2 = tasks_mod._run_pairs_cycle(strategy, date(2024, 12, 3), members)

    history = PairZHistory.objects.filter(pair=pair).order_by("as_of_date")
    assert history.count() == 2, f"expected 2 z-history rows, got {history.count()}"
    # Diagnostics expose a non-empty z_history array for the pair.
    target = PortfolioTarget.objects.get(pk=res2["target_id"])
    op = (target.beta_diagnostics.get("open_pairs") or [])
    assert len(op) == 1
    assert len(op[0].get("z_history") or []) >= 1


class _DriftedDataProvider:
    """Synthetic prices where leg A and leg B no longer move with the
    entry β. We pick offsets such that:
      • the regression of ln(A) on ln(B) over the window gives β_now ≈ 1.5
        (entry β was 1.0 → drift = +50%)
      • today's spread = ln(A_today) − 1.0·ln(B_today) ≈ 0, so |z| ≈ 0
        and the cycle closes the pair as 'reverted', exercising the
        drift-computation code path."""

    def get_daily_bars(self, ticker, start, end, *, as_of):
        # Final values chosen so ln(A_final) = ln(B_final) → spread@today = 0.
        bars = []
        for i in range(260):
            if ticker == "A":
                # log(A) = 2.870 + 1.5·(i·0.001); A_final = exp(3.2585)
                bars.append(_FakeBar(close=math.exp(2.870 + 1.5 * i * 0.001)))
            elif ticker == "B":
                # log(B) = 3.000 + (i·0.001); B_final = exp(3.259)
                bars.append(_FakeBar(close=math.exp(3.000 + i * 0.001)))
            else:
                bars.append(_FakeBar(close=100.0))
        return bars


def test_hedge_ratio_drift_recorded_on_close(monkeypatch, pairs_strategy):
    """A pair whose underlying β has materially shifted by the time it
    closes must record a non-trivial `hedge_ratio_drift_pct`."""
    strategy, pf = pairs_strategy
    pair = Pair.objects.create(
        strategy=strategy,
        leg_a_ticker="A", leg_b_ticker="B", sector="X",
        cointegration_p_value=0.01, correlation=0.95,
        hedge_ratio=1.0,                          # entry β
        spread_mean=0.0, spread_std=0.5,
        spread_window_days=120,
        entry_date=date(2024, 10, 1), entry_z=2.5,
        status="open",
    )
    from apps.portfolios import tasks as tasks_mod
    monkeypatch.setattr(tasks_mod, "get_fmp_provider", lambda *a, **kw: _DriftedDataProvider())
    monkeypatch.setattr(
        tasks_mod, "screen_pairs",
        lambda *_a, **_kw: ([], {"n_pairs_evaluated": 0, "n_pairs_cointegrated": 0,
                                  "n_pairs_above_entry_z": 0, "synthetic_tickers": []}),
    )
    members = tasks_mod._active_members(strategy, date(2024, 12, 2))
    tasks_mod._run_pairs_cycle(strategy, date(2024, 12, 2), members)

    pair.refresh_from_db()
    assert pair.status == "closed"
    assert pair.hedge_ratio_drift_pct is not None
    # Entry β=1.0, current β≈1.5 → drift ≈ +50%. Allow generous tolerance for
    # numerical residuals in the synthetic series.
    assert pair.hedge_ratio_drift_pct > 0.20, \
        f"expected material positive drift, got {pair.hedge_ratio_drift_pct}"


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
    from apps.portfolios import tasks as tasks_mod
    monkeypatch.setattr(tasks_mod, "get_fmp_provider", lambda *a, **kw: _FakeDataProvider())

    from apps.portfolios.tasks import _active_members, _run_pairs_cycle
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
