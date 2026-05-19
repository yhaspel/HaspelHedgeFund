"""Unit tests for the P2e Constructor, Rebalancer, Screener, and borrow stub."""
from __future__ import annotations

from datetime import date

import pytest

from apps.portfolios.borrow import HTB_TICKERS, StubBorrowProvider
from apps.portfolios.construction import (
    Candidate,
    Constraints,
    construct,
)
from apps.portfolios.rebalance import (
    CurrentPosition,
    RebalanceConfig,
    compute_orders,
)
from hedgefund_agents.screener.screener_agent import ScreenerAbort, run_screener


def _c(t, side, action, conf=80, sector="Tech", veto=None):
    return Candidate(
        ticker=t, sector=sector, side=side, action=action, confidence=conf,
        quality_weight=1.0, veto_reason=veto,
    )


def test_constructor_all_bullish_equal_weights():
    cands = [_c(f"T{i}", "long", "buy", conf=80) for i in range(5)]
    out = construct(cands, Constraints(target_gross_pct=1.0, target_net_pct=1.0,
                                          max_position_pct=1.0, max_sector_pct=1.0,
                                          min_position_pct=0.0001))
    # All in one bucket; equal confidence → equal weights summing to (1+1)/2 = 1.0
    assert pytest.approx(out.gross_pct, abs=1e-6) == 1.0
    assert pytest.approx(out.net_pct, abs=1e-6) == 1.0
    weights = list(out.target_weights.values())
    assert all(abs(w - weights[0]) < 1e-6 for w in weights)


def test_constructor_long_short_split():
    cands = (
        [_c(f"L{i}", "long", "buy", sector=f"S{i%3}") for i in range(6)] +
        [_c(f"S{i}", "short", "open_short", sector=f"S{i%3}") for i in range(4)]
    )
    out = construct(cands, Constraints(target_gross_pct=1.50, target_net_pct=0.50,
                                          max_position_pct=0.20, max_sector_pct=1.50,
                                          min_position_pct=0.001))
    # Long bucket should sum to (1.5+0.5)/2 = 1.0; short to (1.5-0.5)/2 = 0.5 (negative)
    long_sum = sum(w for w in out.target_weights.values() if w > 0)
    short_sum = sum(w for w in out.target_weights.values() if w < 0)
    assert pytest.approx(long_sum, abs=0.01) == 1.0
    assert pytest.approx(short_sum, abs=0.01) == -0.5


def test_constructor_borrow_veto_drops_short():
    cands = [
        _c("AAPL", "long", "buy"),
        _c("GME", "short", "open_short", veto="borrow_not_locatable"),
    ]
    out = construct(cands, Constraints(min_position_pct=0.0001))
    assert "GME" not in out.target_weights
    assert any(r.get("ticker") == "GME" for r in out.rejected)


def test_constructor_per_name_cap_enforced():
    cands = [_c(f"T{i}", "long", "buy", conf=80) for i in range(3)]
    cap = 0.10
    out = construct(cands, Constraints(target_gross_pct=1.0, target_net_pct=1.0,
                                          max_position_pct=cap, max_sector_pct=1.0,
                                          min_position_pct=0.0001))
    for w in out.target_weights.values():
        assert abs(w) <= cap + 1e-9


def test_rebalancer_empty_portfolio_opens_all():
    target = {"AAPL": 0.10, "MSFT": -0.05}
    orders = compute_orders(
        current=[],
        target_weights=target,
        cfg=RebalanceConfig(
            portfolio_value=100_000.0,
            last_close={"AAPL": 200.0, "MSFT": 400.0},
            min_trade_notional_usd=50.0,
        ),
    )
    assert {o.ticker for o in orders} == {"AAPL", "MSFT"}
    assert all(o.reason == "open" and o.sequence == 2 for o in orders)
    aapl = next(o for o in orders if o.ticker == "AAPL")
    assert aapl.side == "buy"
    msft = next(o for o in orders if o.ticker == "MSFT")
    assert msft.side == "short"


def test_rebalancer_close_first_then_resize():
    current = [
        CurrentPosition(ticker="AAPL", quantity=10, avg_cost=150, sector="Tech"),
        CurrentPosition(ticker="OLD", quantity=5, avg_cost=50, sector="Tech"),
    ]
    target = {"AAPL": 0.20, "NEW": 0.10}
    orders = compute_orders(
        current=current, target_weights=target,
        cfg=RebalanceConfig(
            portfolio_value=100_000.0,
            last_close={"AAPL": 200.0, "OLD": 100.0, "NEW": 50.0},
            min_trade_notional_usd=10.0,
        ),
    )
    seqs = {o.ticker: o.sequence for o in orders}
    assert seqs["OLD"] == 0  # closed first
    assert seqs.get("AAPL") == 1  # resize
    assert seqs.get("NEW") == 2  # open


def test_rebalancer_identical_current_and_target_no_orders():
    current = [
        CurrentPosition(ticker="AAPL", quantity=10, avg_cost=200, sector="Tech"),
    ]
    target = {"AAPL": 10 * 200 / 100_000.0}  # exactly current
    orders = compute_orders(
        current=current, target_weights=target,
        cfg=RebalanceConfig(
            portfolio_value=100_000.0,
            last_close={"AAPL": 200.0},
            min_trade_notional_usd=10.0,
        ),
    )
    assert orders == []


def test_screener_universe_cost_guardrail():
    members = [(f"T{i}", "X") for i in range(2001)]
    with pytest.raises(ScreenerAbort):
        run_screener(members=members, as_of_date=date(2024, 1, 1),
                     top_k_longs=10, top_k_shorts=5)


def test_screener_idempotent_synthetic():
    # The provider has nothing for these tickers, so the synthetic fallback
    # kicks in and the ranking is byte-stable for a given as_of_date.
    members = [(f"FAKE{i}", "X") for i in range(50)]
    a = run_screener(members=members, as_of_date=date(2024, 1, 2),
                     top_k_longs=10, top_k_shorts=5)
    b = run_screener(members=members, as_of_date=date(2024, 1, 2),
                     top_k_longs=10, top_k_shorts=5)
    assert [c["ticker"] for c in a["long_candidates"]] == [
        c["ticker"] for c in b["long_candidates"]
    ]
    assert [c["ticker"] for c in a["short_candidates"]] == [
        c["ticker"] for c in b["short_candidates"]
    ]


def test_stub_borrow_provider_blacklist():
    sb = StubBorrowProvider()
    for t in HTB_TICKERS:
        info = sb.quote(t, date(2024, 1, 1))
        assert info.is_locatable is False
    info = sb.quote("AAPL", date(2024, 1, 1))
    assert info.is_locatable is True
