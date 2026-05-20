"""P2g — concentrated long-only construction tests."""
from __future__ import annotations

import pytest

from apps.portfolios.construction import (
    Candidate,
    Constraints,
    PMActionWhitelistError,
    construct_concentrated_long,
)


def _c(t, action="buy", conf=80, sector="Tech", side="long"):
    return Candidate(
        ticker=t, sector=sector, side=side, action=action, confidence=conf,
        quality_weight=1.0, veto_reason=None,
    )


def _cons(**kw):
    base = dict(
        target_gross_pct=1.0,
        target_net_pct=1.0,
        max_position_pct=0.25,
        max_sector_pct=0.60,
        min_position_pct=0.05,
    )
    base.update(kw)
    return Constraints(**base)


def test_target_created_when_enough_high_conviction():
    cands = [_c(f"T{i}", conf=80, sector=f"S{i%2}") for i in range(7)]
    out = construct_concentrated_long(
        cands, _cons(), min_positions=5, max_positions=10,
        min_aggregate_confidence=0.65,
    )
    assert out.outcome == "target_created"
    assert 5 <= len(out.target_weights) <= 10
    for w in out.target_weights.values():
        assert 0.05 - 1e-9 <= w <= 0.25 + 1e-9
    assert abs(sum(out.target_weights.values()) - 1.0) < 0.05


def test_held_existing_book_when_too_few_clear_threshold():
    cands = [_c(f"T{i}", conf=80) for i in range(3)] + [_c(f"L{i}", conf=40) for i in range(5)]
    out = construct_concentrated_long(
        cands, _cons(), min_positions=5, max_positions=10,
        min_aggregate_confidence=0.65,
    )
    assert out.outcome == "held_existing_book"
    assert out.target_weights == {}
    assert any(r.get("reason") == "insufficient_high_conviction_candidates" for r in out.rejected)


def test_max_position_cap_enforced():
    cands = [_c(f"T{i}", conf=95) for i in range(6)]
    out = construct_concentrated_long(
        cands, _cons(max_position_pct=0.20), min_positions=5, max_positions=10,
        min_aggregate_confidence=0.65,
    )
    for w in out.target_weights.values():
        assert w <= 0.20 + 1e-9


def test_min_position_floor_raises_thin_weights():
    # Many candidates → naive equal weights below floor, must be raised
    cands = [_c(f"T{i}", conf=80, sector=f"S{i}") for i in range(15)]
    out = construct_concentrated_long(
        cands, _cons(target_gross_pct=1.0, min_position_pct=0.07,
                    max_position_pct=0.25, max_sector_pct=0.99),
        min_positions=5, max_positions=15, min_aggregate_confidence=0.65,
    )
    assert out.outcome == "target_created"
    for w in out.target_weights.values():
        assert w >= 0.07 - 1e-9


def test_short_candidate_raises_whitelist_error():
    cands = [_c("AAA", action="buy", conf=80)]
    cands.append(_c("BAD", action="open_short", side="short", conf=80))
    with pytest.raises(PMActionWhitelistError):
        construct_concentrated_long(
            cands, _cons(), min_positions=1, max_positions=5,
            min_aggregate_confidence=0.65,
        )


def test_idempotent():
    cands = [_c(f"T{i}", conf=70 + i) for i in range(8)]
    a = construct_concentrated_long(cands, _cons(), min_positions=5, max_positions=10,
                                    min_aggregate_confidence=0.65)
    b = construct_concentrated_long(cands, _cons(), min_positions=5, max_positions=10,
                                    min_aggregate_confidence=0.65)
    assert a.target_weights == b.target_weights
