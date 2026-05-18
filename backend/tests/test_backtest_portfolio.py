"""SimulatedPortfolio mechanics: fills, marks, splits, dividends, turnover."""
from __future__ import annotations

from apps.backtests.portfolio import SimulatedPortfolio


def test_initial_state() -> None:
    pf = SimulatedPortfolio(starting_cash=100_000)
    assert pf.cash == 100_000
    assert pf.total_value == 100_000
    assert pf.snapshot() == []


def test_buy_then_mark() -> None:
    pf = SimulatedPortfolio(starting_cash=100_000, commission_bps=0, spread_bps=0)
    fills = pf.execute(
        [{"ticker": "AAPL", "action": "buy", "target_weight_pct": 10.0, "target_quantity": 0}],
        fill_prices={"AAPL": 100.0},
    )
    assert len(fills) == 1
    p = pf.positions["AAPL"]
    assert abs(p.qty - 100.0) < 1e-6  # 10% of 100k / $100
    assert pf.cash == 90_000
    pf.mark_to_market({"AAPL": 120.0})
    assert pf.total_value == 90_000 + 100 * 120


def test_split_doubles_qty_halves_cost() -> None:
    pf = SimulatedPortfolio(starting_cash=100_000, commission_bps=0, spread_bps=0)
    pf.execute(
        [{"ticker": "AAPL", "action": "buy", "target_weight_pct": 10.0}],
        fill_prices={"AAPL": 200.0},
    )
    qty_before = pf.positions["AAPL"].qty
    avg_before = pf.positions["AAPL"].avg_cost
    pf.apply_split("AAPL", 2.0)
    assert abs(pf.positions["AAPL"].qty - qty_before * 2.0) < 1e-6
    assert abs(pf.positions["AAPL"].avg_cost - avg_before / 2.0) < 1e-6


def test_dividend_adds_cash() -> None:
    pf = SimulatedPortfolio(starting_cash=100_000, commission_bps=0, spread_bps=0)
    pf.execute(
        [{"ticker": "AAPL", "action": "buy", "target_weight_pct": 10.0}],
        fill_prices={"AAPL": 100.0},
    )
    cash_before = pf.cash
    qty = pf.positions["AAPL"].qty
    pf.apply_dividend("AAPL", 1.50)
    assert abs(pf.cash - (cash_before + qty * 1.50)) < 1e-6


def test_commission_and_turnover_tracked() -> None:
    pf = SimulatedPortfolio(starting_cash=100_000, commission_bps=10.0, spread_bps=0)
    pf.execute(
        [{"ticker": "AAPL", "action": "buy", "target_weight_pct": 10.0}],
        fill_prices={"AAPL": 100.0},
    )
    # 10% of 100k = $10k notional; 10 bps = $10 commission. Cash = 100k - 10k - 10.
    assert abs(pf.cash - (100_000 - 10_000 - 10.0)) < 1e-3
    assert pf.turnover_total_notional > 0


def test_sell_exits_to_zero() -> None:
    pf = SimulatedPortfolio(starting_cash=100_000, commission_bps=0, spread_bps=0)
    pf.execute(
        [{"ticker": "AAPL", "action": "buy", "target_weight_pct": 10.0}],
        fill_prices={"AAPL": 100.0},
    )
    pf.execute(
        [{"ticker": "AAPL", "action": "sell", "target_weight_pct": 0.0}],
        fill_prices={"AAPL": 110.0},
    )
    assert pf.positions["AAPL"].qty == 0.0
