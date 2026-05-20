"""Feasibility of per-flavor default thresholds.

Each strategy run should yield results under a *realistic* council confidence
distribution. These tests synthesise candidates whose confidence mix mirrors
what we see from Llama 3.3 70B + Haiku council outputs (most votes in the
50-70 band, fewer above 75), and assert that each flavor's construction call
produces non-empty target_weights using the **model-field defaults**.

Regressions caught: tightening min_aggregate_confidence, lowering top_k,
hard-coding overlap thresholds too low, or any change that re-introduces the
"runs silently produce empty books" failure mode.
"""
from __future__ import annotations

from apps.portfolios.construction import (
    Candidate,
    Constraints,
    construct,
    construct_concentrated_long,
    construct_market_neutral,
    construct_sector_rotation,
)

# Confidence mix observed in council outputs: skewed to 50-70 band.
_REALISTIC_CONFS = [45, 50, 52, 55, 58, 60, 62, 65, 68, 70, 72, 75, 78]


def _long_cands(n: int = 10, sector_pool=("Tech", "Health", "Fin", "Cons", "Indu")):
    return [
        Candidate(
            ticker=f"L{i}",
            sector=sector_pool[i % len(sector_pool)],
            side="long",
            action="buy",
            confidence=_REALISTIC_CONFS[i % len(_REALISTIC_CONFS)],
            quality_weight=1.0,
            veto_reason=None,
        )
        for i in range(n)
    ]


def _short_cands(n: int = 5):
    return [
        Candidate(
            ticker=f"S{i}", sector="Tech", side="short", action="open_short",
            confidence=55 + i * 3, quality_weight=1.0, veto_reason=None,
        )
        for i in range(n)
    ]


def _default_constraints() -> Constraints:
    # Mirrors PortfolioStrategy field defaults.
    return Constraints(
        target_gross_pct=1.50,
        target_net_pct=0.50,
        max_position_pct=0.03,
        max_sector_pct=0.25,
        min_position_pct=0.005,
    )


def test_p2e_long_short_default_yields_book():
    """P2e with default constraints + realistic confidences → non-empty book."""
    cands = _long_cands(10) + _short_cands(5)
    out = construct(cands, _default_constraints())
    assert out.target_weights, "P2e produced empty book with default constraints"
    longs = [w for w in out.target_weights.values() if w > 0]
    shorts = [w for w in out.target_weights.values() if w < 0]
    assert longs, "P2e produced no long positions"
    assert shorts, "P2e produced no short positions"


def test_p2f_market_neutral_default_yields_book():
    """P2f with default tolerances + realistic confidences → non-empty book."""
    cands = _long_cands(10) + _short_cands(5)
    # Modest betas so the alpha rescale stays in band.
    betas = {c.ticker: 1.0 for c in cands}
    out = construct_market_neutral(
        cands, _default_constraints(), betas,
        tol_dollar=0.02, tol_beta=0.05,
    )
    assert out.target_weights, "P2f produced empty book with default tolerances"
    longs = [w for w in out.target_weights.values() if w > 0]
    shorts = [w for w in out.target_weights.values() if w < 0]
    assert longs and shorts, "P2f must have both legs"


def test_p2g_concentrated_default_yields_book():
    """P2g with NEW defaults (min_conf=0.55, min_pos=3) → non-empty book.

    With the old defaults (0.65 / 5) only ~5 of 10 candidates clear the bar
    when confidences are realistically distributed; under 0.55/3 the survivor
    set is comfortably above the floor.
    """
    cands = _long_cands(10)
    # Concentrated long uses its own constraints (smaller universe, looser caps).
    cons = Constraints(
        target_gross_pct=1.00, target_net_pct=1.00,
        max_position_pct=0.25, max_sector_pct=0.60, min_position_pct=0.02,
    )
    out = construct_concentrated_long(
        cands, cons,
        min_positions=3,                  # new default
        max_positions=15,
        min_aggregate_confidence=0.55,    # new default
    )
    assert out.outcome == "target_created", (
        f"P2g returned outcome={out.outcome!r} with realistic confidences "
        f"under the new defaults — feasibility regression."
    )
    assert out.target_weights, "P2g produced empty book"
    assert len(out.target_weights) >= 3


def test_p2g_old_defaults_would_have_failed():
    """Regression guard: old defaults (0.65/5) DO yield empty book under the
    same realistic-confidence input. Documents WHY we relaxed them."""
    cands = _long_cands(10)
    cons = Constraints(
        target_gross_pct=1.00, target_net_pct=1.00,
        max_position_pct=0.25, max_sector_pct=0.60, min_position_pct=0.02,
    )
    out = construct_concentrated_long(
        cands, cons,
        min_positions=5,
        max_positions=15,
        min_aggregate_confidence=0.65,
    )
    assert out.outcome == "held_existing_book"
    assert not out.target_weights


def test_p2h_sector_rotation_default_yields_book():
    """P2h with default per_etf bounds + realistic confidences → non-empty book.

    Uses SPDR sector tickers so the hard-coded overlap pairs don't all collapse
    the survivor set.
    """
    tickers = ["XLK", "XLF", "XLV", "XLY", "XLE", "XLI", "XLU", "XLP", "XLB", "XLRE"]
    cands = [
        Candidate(
            ticker=t, sector=t, side="long", action="buy",
            confidence=_REALISTIC_CONFS[i % len(_REALISTIC_CONFS)],
            quality_weight=1.0, veto_reason=None,
        )
        for i, t in enumerate(tickers)
    ]
    out = construct_sector_rotation(
        cands,
        target_gross_pct=1.00,
        per_etf_max_pct=0.30,
        per_etf_min_pct=0.05,
        max_etfs_held=6,
    )
    assert out.target_weights, "P2h produced empty ETF book with default bounds"
    assert 1 <= len(out.target_weights) <= 6
