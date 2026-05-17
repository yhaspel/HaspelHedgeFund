"""Valuation arithmetic unit tests."""
from __future__ import annotations

from hedgefund_agents.analytical.valuation import (
    compute_dcf,
    compute_multiples,
    compute_residual_income,
)


def test_dcf_positive_for_positive_fcf() -> None:
    v = compute_dcf(fcf_ttm=1000.0, growth=0.05)
    assert v is not None and v > 1000.0


def test_dcf_none_for_negative_fcf() -> None:
    assert compute_dcf(fcf_ttm=-50.0, growth=0.05) is None


def test_dcf_requires_wacc_gt_terminal() -> None:
    assert compute_dcf(fcf_ttm=100.0, growth=0.05, wacc=0.02, terminal_g=0.03) is None


def test_multiples_uses_pe_peer() -> None:
    assert compute_multiples(net_income_ttm=100.0, pe_peer=20.0) == 2000.0


def test_residual_income_floors_to_book_when_ri_negative() -> None:
    v = compute_residual_income(equity=1000.0, net_income_ttm=50.0, cost_of_equity=0.10)
    assert v == 1000.0  # earns below cost of equity => floor to book


def test_residual_income_positive_when_ri_positive() -> None:
    v = compute_residual_income(equity=1000.0, net_income_ttm=200.0, cost_of_equity=0.10)
    assert v is not None and v > 1000.0
