"""Risk Manager hard-cap + veto unit tests (pure rules; no LLM)."""
from __future__ import annotations

import pytest

from hedgefund_agents.risk.risk_manager import (
    RiskLimits,
    StubPortfolio,
    apply_hard_caps,
)


def test_default_portfolio_allows_full_cap() -> None:
    triggered, cap, veto = apply_hard_caps(StubPortfolio(), RiskLimits(), "AAPL")
    assert triggered == []
    assert cap == 0.20
    assert veto is False


def test_drawdown_breach_triggers_veto() -> None:
    p = StubPortfolio(drawdown_pct=0.30)
    _, cap, veto = apply_hard_caps(p, RiskLimits(), "AAPL")
    assert veto is True
    assert cap >= 0.0


def test_existing_position_reduces_headroom() -> None:
    p = StubPortfolio(positions={"AAPL": 18_000.0})  # 18% existing
    triggered, cap, veto = apply_hard_caps(p, RiskLimits(max_position_pct=0.20), "AAPL")
    assert "max_position_pct" in triggered
    assert cap == pytest.approx(0.02)
    assert veto is False


def test_no_headroom_vetoes() -> None:
    p = StubPortfolio(positions={"AAPL": 20_000.0})  # at cap
    _, cap, veto = apply_hard_caps(p, RiskLimits(), "AAPL")
    assert cap == 0.0
    assert veto is True


def test_sector_concentration_caps() -> None:
    p = StubPortfolio(
        sector="Tech",
        sector_exposure={"Tech": 38_000.0},  # 38%
    )
    triggered, cap, veto = apply_hard_caps(p, RiskLimits(max_sector_pct=0.40), "AAPL")
    assert "max_sector_pct" in triggered
    assert cap == pytest.approx(0.02)
