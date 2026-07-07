"""Leverage / metric hardening guards (backtest engine).

Covers the latent risks surfaced by the bt30 drawdown audit:
  - SimulatedPortfolio.execute must cap gross exposure at max_gross x equity
    and never borrow (cash floored at 0), since the PM emits per-name weights
    that are not normalized to a gross budget.
  - metrics.drawdown_pct must never exceed 100%, and the stitched return series
    must not emit sign-flipped ratios across a non-positive equity crossing.
These are pure-python (no DB).
"""
from __future__ import annotations

import pytest

from apps.backtests.metrics import _step_returns, drawdown_pct
from apps.backtests.portfolio import SimulatedPortfolio


def _gross(pf: SimulatedPortfolio) -> float:
    return sum(abs(p.market_value) for p in pf.positions.values())


def test_execute_caps_gross_exposure_at_100pct() -> None:
    # 14 names each requested at 20% => 280% intended gross. The cap must scale
    # the book back to <= 100% of equity, and cash must not go negative.
    pf = SimulatedPortfolio(starting_cash=100_000, commission_bps=0, spread_bps=0)
    tickers = [f"T{i}" for i in range(14)]
    decisions = [
        {"ticker": t, "action": "buy", "target_weight_pct": 20.0} for t in tickers
    ]
    prices = {t: 100.0 for t in tickers}
    pf.execute(decisions, fill_prices=prices)
    assert _gross(pf) <= 100_000 + 1.0  # <= 100% gross
    assert _gross(pf) > 95_000          # cap binds at ~100%, not zero
    assert pf.cash >= -1.0              # no implicit borrowing


def test_execute_short_book_gross_capped() -> None:
    # Mixed long/short summing to 200% gross must be scaled to 100%.
    pf = SimulatedPortfolio(starting_cash=100_000, commission_bps=0, spread_bps=0)
    decisions = [
        {"ticker": "LONG", "action": "buy", "target_weight_pct": 100.0},
        {"ticker": "SHRT", "action": "open_short", "target_weight_pct": -100.0},
    ]
    prices = {"LONG": 50.0, "SHRT": 50.0}
    pf.execute(decisions, fill_prices=prices)
    assert _gross(pf) <= 100_000 + 1.0
    assert pf.positions["SHRT"].qty < 0  # short established (negative qty)
    assert pf.positions["LONG"].qty > 0


def test_execute_rotation_funded_by_sells_no_borrow() -> None:
    # Fully invested in A, rotate 100% into B. Sells must run before buys so the
    # buy is funded by the same-day sale rather than borrowing.
    pf = SimulatedPortfolio(starting_cash=100_000, commission_bps=0, spread_bps=0)
    pf.execute(
        [{"ticker": "A", "action": "buy", "target_weight_pct": 100.0}],
        fill_prices={"A": 100.0},
    )
    assert abs(pf.cash) < 1.0  # ~fully invested
    pf.execute(
        [
            {"ticker": "A", "action": "sell", "target_weight_pct": 0.0},
            {"ticker": "B", "action": "buy", "target_weight_pct": 100.0},
        ],
        fill_prices={"A": 100.0, "B": 200.0},
    )
    assert pf.positions["A"].qty == 0.0
    assert pf.positions["B"].qty > 0
    assert pf.cash >= -1.0
    assert _gross(pf) <= 100_000 + 1.0


def test_small_position_unaffected_by_guards() -> None:
    # Guards are no-ops well within budget: a single 10% buy is unchanged.
    pf = SimulatedPortfolio(starting_cash=100_000, commission_bps=0, spread_bps=0)
    pf.execute(
        [{"ticker": "AAPL", "action": "buy", "target_weight_pct": 10.0}],
        fill_prices={"AAPL": 100.0},
    )
    assert abs(pf.positions["AAPL"].qty - 100.0) < 1e-6
    assert pf.cash == 90_000


def test_drawdown_pct_never_exceeds_one() -> None:
    assert drawdown_pct([100.0, -50.0]) == 1.0     # account wiped => 100%, not 150%
    assert drawdown_pct([100.0, -200.0]) == 1.0    # not 300%
    assert drawdown_pct([100.0, 50.0]) == 0.5      # normal case unchanged
    assert drawdown_pct([100.0, 120.0, 60.0]) == 0.5
    assert drawdown_pct([]) == 0.0


def test_step_returns_guard_on_zero_crossing() -> None:
    # Strictly-positive series == the old equity[i]/equity[i-1]-1 behavior.
    assert _step_returns([100.0, 110.0, 99.0]) == pytest.approx([0.1, -0.1])
    # Crossing to <= 0 caps the step at -100% and stops compounding.
    r = _step_returns([100.0, 50.0, -5.0])
    assert r == pytest.approx([-0.5, -1.0])
    # No sign-flipped ratio across zero.
    assert all(x >= -1.0 for x in r)


def test_execute_allows_intentional_leverage_up_to_max_gross() -> None:
    # max_gross>1.0 lets a buy borrow (negative cash) up to the leverage limit.
    pf = SimulatedPortfolio(starting_cash=100_000, commission_bps=0, spread_bps=0)
    tickers = [f"T{i}" for i in range(10)]
    decisions = [
        {"ticker": t, "action": "buy", "target_weight_pct": 20.0} for t in tickers
    ]  # 200% intended gross
    prices = {t: 100.0 for t in tickers}
    pf.execute(decisions, fill_prices=prices, max_gross=2.0)
    assert _gross(pf) == pytest.approx(200_000, rel=0.02)   # ~200% of equity
    assert pf.cash == pytest.approx(-100_000, abs=1.0)      # borrowed ~100% of equity
    assert pf.total_value == pytest.approx(100_000, abs=1.0)  # equity unchanged at entry marks


def test_execute_caps_leverage_at_max_gross() -> None:
    # Intended 300% gross is clamped to the 1.5x leverage limit.
    pf = SimulatedPortfolio(starting_cash=100_000, commission_bps=0, spread_bps=0)
    decisions = [
        {"ticker": f"T{i}", "action": "buy", "target_weight_pct": 30.0} for i in range(10)
    ]
    prices = {f"T{i}": 100.0 for i in range(10)}
    pf.execute(decisions, fill_prices=prices, max_gross=1.5)
    assert _gross(pf) <= 150_000 + 1.0
    assert pf.cash >= -50_000 - 1.0                          # borrowing capped at 0.5×equity


# --- P11 E2: financing drag on the margin borrow --------------------------

def _lever_2x(financing_bps: float) -> SimulatedPortfolio:
    """A 2× long book: cash borrowed ~= -100k (the margin debit)."""
    pf = SimulatedPortfolio(
        starting_cash=100_000, commission_bps=0, spread_bps=0,
        financing_bps=financing_bps,
    )
    decisions = [
        {"ticker": f"T{i}", "action": "buy", "target_weight_pct": 20.0} for i in range(10)
    ]
    pf.execute(decisions, fill_prices={f"T{i}": 100.0 for i in range(10)}, max_gross=2.0)
    return pf


def test_accrue_financing_charges_daily_carry_on_borrow() -> None:
    # 2× book borrows ~100k; one day of 2%/yr carry ≈ 100_000 * 0.02 / 252.
    pf = _lever_2x(financing_bps=200)
    assert pf.cash == pytest.approx(-100_000, abs=1.0)
    charge = pf.accrue_financing()
    assert charge == pytest.approx(100_000 * 0.02 / 252, rel=1e-6)
    # A full year of daily accrual ≈ 2% of the borrow (compounding grows the debt
    # slightly, so the total is a touch above 2000).
    for _ in range(251):
        pf.accrue_financing()
    total_carry = -100_000 - pf.cash
    assert 2_000 <= total_carry <= 2_030


def test_accrue_financing_noop_when_unlevered() -> None:
    # A 50% book holds positive cash (no borrow) ⇒ zero financing even at 2%/yr.
    pf = SimulatedPortfolio(
        starting_cash=100_000, commission_bps=0, spread_bps=0, financing_bps=200,
    )
    pf.execute(
        [{"ticker": "AAPL", "action": "buy", "target_weight_pct": 50.0}],
        fill_prices={"AAPL": 100.0},
    )
    assert pf.cash == pytest.approx(50_000, abs=1.0)
    assert pf.accrue_financing() == 0.0
    assert pf.cash == pytest.approx(50_000, abs=1.0)


def test_accrue_financing_zero_rate_is_noop() -> None:
    # financing_bps=0 (the dataclass default) never charges, even when levered.
    assert SimulatedPortfolio(starting_cash=1.0).financing_bps == 0.0
    pf = _lever_2x(financing_bps=0)
    assert pf.accrue_financing() == 0.0
