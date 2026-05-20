"""P2h — sector / thematic ETF rotation tests."""
from __future__ import annotations

import pytest

from apps.portfolios.construction import (
    Candidate,
    PMActionWhitelistError,
    construct_sector_rotation,
)
from hedgefund_agents.screener.sector_features import (
    SectorFeatures,
    macro_regime_vector,
    sector_score,
)


def _c(t, action="buy", conf=80, sector="Technology"):
    return Candidate(
        ticker=t, sector=sector, side="long", action=action, confidence=conf,
        quality_weight=1.0, veto_reason=None,
    )


def test_sector_rotation_builds_long_only_book():
    cands = [
        _c("XLK", conf=90, sector="Technology"),
        _c("XLF", conf=80, sector="Financials"),
        _c("XLE", conf=70, sector="Energy"),
        _c("XLV", conf=60, sector="Health Care"),
        _c("XLY", conf=50, sector="Consumer Discretionary"),
    ]
    out = construct_sector_rotation(
        cands, target_gross_pct=1.0, per_etf_max_pct=0.30,
        per_etf_min_pct=0.05, max_etfs_held=6,
    )
    assert set(out.target_weights.keys()) == {"XLK", "XLF", "XLE", "XLV", "XLY"}
    for w in out.target_weights.values():
        assert 0.05 - 1e-9 <= w <= 0.30 + 1e-9
    assert abs(sum(out.target_weights.values()) - 1.0) < 0.01


def test_overlap_penalty_drops_lower_conviction_etf():
    cands = [
        _c("XLK", conf=90, sector="Technology"),
        _c("SOXX", conf=75, sector="Technology"),  # ~50% overlap with XLK
        _c("XLF", conf=70, sector="Financials"),
    ]
    out = construct_sector_rotation(cands, max_etfs_held=6)
    assert "SOXX" not in out.target_weights
    assert "XLK" in out.target_weights
    assert any(d["ticker"] == "SOXX" and d["kept"] == "XLK" for d in out.overlap_dropped)


def test_short_side_raises_whitelist_error():
    cands = [
        Candidate(ticker="XLE", sector="Energy", side="short", action="open_short",
                  confidence=80, quality_weight=1.0, veto_reason=None),
    ]
    with pytest.raises(PMActionWhitelistError):
        construct_sector_rotation(cands)


def test_max_etfs_held_caps_book():
    cands = [_c(f"E{i}", conf=80 - i, sector=f"S{i}") for i in range(15)]
    out = construct_sector_rotation(cands, max_etfs_held=4, per_etf_max_pct=0.40)
    assert len(out.target_weights) == 4


def test_regime_fit_uses_macro_snapshot():
    class Snap:
        growth_quadrant = "early expansion"
        inflation_regime = "low"
        yield_curve_state = "steep"
        policy_stance = "accommodative"
    v = macro_regime_vector(Snap())
    assert v["early_cycle"] == 1.0
    assert v["rising_rates"] == 0.0


def test_sector_score_prefers_positive_relative_momentum():
    high = SectorFeatures(ticker="A", sector="Technology",
                          relative_momentum_3m=0.10, regime_fit=0.5)
    low = SectorFeatures(ticker="B", sector="Technology",
                         relative_momentum_3m=-0.10, regime_fit=-0.5)
    assert sector_score(high) > sector_score(low)


def test_sector_rotation_per_etf_min_floor_applied():
    cands = [
        _c("A", conf=99), _c("B", conf=98), _c("C", conf=97),
        _c("D", conf=10), _c("E", conf=9),
    ]
    out = construct_sector_rotation(
        cands, target_gross_pct=1.0, per_etf_min_pct=0.10, max_etfs_held=5,
    )
    for w in out.target_weights.values():
        assert w >= 0.10 - 1e-9


def test_idempotent_same_inputs_same_outputs():
    cands = [_c(f"E{i}", conf=80 - i) for i in range(6)]
    r1 = construct_sector_rotation(cands, max_etfs_held=5)
    r2 = construct_sector_rotation(cands, max_etfs_held=5)
    assert r1.target_weights == r2.target_weights
