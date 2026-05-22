"""P2j — risk-parity / multi-asset-lite tests."""
from __future__ import annotations

from apps.portfolios.construction import construct_risk_parity


def test_inverse_vol_weighting_matches_expected():
    sleeves = [("A", "Equity"), ("B", "Equity"), ("C", "Rates")]
    vols = {"A": 0.005, "B": 0.010, "C": 0.020}
    out = construct_risk_parity(
        sleeves, vols, target_gross_pct=1.0,
        per_sleeve_max_pct=1.0, per_sleeve_min_pct=0.0,
    )
    # Inverse weights raw: 200, 100, 50 → normalised 4/7, 2/7, 1/7.
    assert abs(out.target_weights["A"] - 4 / 7) < 1e-6
    assert abs(out.target_weights["B"] - 2 / 7) < 1e-6
    assert abs(out.target_weights["C"] - 1 / 7) < 1e-6
    assert abs(out.gross_pct - 1.0) < 1e-9


def test_unavailable_vol_drops_sleeve():
    sleeves = [("A", "Equity"), ("B", "Rates")]
    out = construct_risk_parity(
        sleeves, {"A": 0.01}, target_gross_pct=1.0,
        per_sleeve_max_pct=1.0, per_sleeve_min_pct=0.0,
    )
    assert "B" not in out.target_weights
    assert any(r["ticker"] == "B" for r in out.rejected)
    assert abs(out.target_weights["A"] - 1.0) < 1e-9


def test_rebalance_band_signals_within_band_on_small_drift():
    sleeves = [("A", "X"), ("B", "X")]
    vols = {"A": 0.01, "B": 0.01}
    # Target should be 0.5/0.5. Current 0.51/0.49 → drift 2% → within 5% band.
    out = construct_risk_parity(
        sleeves, vols, target_gross_pct=1.0,
        per_sleeve_max_pct=1.0, per_sleeve_min_pct=0.0,
        current_weights={"A": 0.51, "B": 0.49},
        rebalance_band_pct=0.05,
    )
    assert out.within_band is True


def test_cold_start_is_always_out_of_band():
    sleeves = [("A", "X")]
    out = construct_risk_parity(
        sleeves, {"A": 0.01}, target_gross_pct=1.0,
        per_sleeve_max_pct=1.0, per_sleeve_min_pct=0.0,
        current_weights=None, rebalance_band_pct=0.05,
    )
    assert out.within_band is False
    assert out.diagnostics["cold_start"] is True


def test_per_sleeve_floor_enforced():
    sleeves = [("A", "X"), ("B", "X"), ("C", "X")]
    # Very low B and C vol would dominate; check floor lifts whoever falls below.
    vols = {"A": 0.001, "B": 0.05, "C": 0.05}
    out = construct_risk_parity(
        sleeves, vols, target_gross_pct=1.0,
        per_sleeve_max_pct=0.90, per_sleeve_min_pct=0.05,
    )
    for w in out.target_weights.values():
        assert w >= 0.05 - 1e-9


def test_diagnostics_include_baseline_version_and_sleeve_rows_p02j():
    """P02j review: diagnostics must include ``baseline_version``,
    ``deterministic_weights``, and a per-sleeve list with vol + weight +
    risk_contribution so the UI can render the sleeve table."""
    sleeves = [("A", "Equity"), ("B", "Rates")]
    vols = {"A": 0.01, "B": 0.005}
    out = construct_risk_parity(
        sleeves, vols, target_gross_pct=1.0,
        per_sleeve_max_pct=1.0, per_sleeve_min_pct=0.0,
    )
    d = out.diagnostics
    assert d["baseline_version"] == "v1"
    assert set(d["deterministic_weights"]) == {"A", "B"}
    rows = {row["ticker"]: row for row in d["sleeves"]}
    assert {"A", "B"}.issubset(rows)
    assert rows["A"]["daily_vol"] == 0.01
    assert rows["A"]["target_weight"] > 0
    # Risk contributions of pure inverse-vol weights are equal across sleeves.
    rc_a = rows["A"]["risk_contribution"]
    rc_b = rows["B"]["risk_contribution"]
    assert abs(rc_a - rc_b) < 1e-9


def test_diagnostics_include_rebalance_skip_reason_when_within_band_p02j():
    sleeves = [("A", "X"), ("B", "X")]
    vols = {"A": 0.01, "B": 0.01}
    out = construct_risk_parity(
        sleeves, vols, target_gross_pct=1.0,
        per_sleeve_max_pct=1.0, per_sleeve_min_pct=0.0,
        current_weights={"A": 0.50, "B": 0.50},
        rebalance_band_pct=0.05,
    )
    assert out.within_band is True
    assert "within ±5% band" in out.diagnostics["rebalance_skip_reason"]
