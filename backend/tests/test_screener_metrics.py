"""Unit tests for the screener's pure metric functions (P3 prereq 3)."""
from __future__ import annotations

from decimal import Decimal

from apps.screener.metrics import (
    adv,
    change_pct,
    distance_from_high,
    distance_from_low,
    dollar_volume,
    gap_pct,
    is_above,
    momentum,
    relative_volume,
)


def test_relative_volume_basic() -> None:
    assert relative_volume(2_000_000, 1_000_000.0) == 2.0


def test_relative_volume_missing_inputs_returns_none() -> None:
    assert relative_volume(None, 1_000_000.0) is None
    assert relative_volume(2_000_000, None) is None
    assert relative_volume(2_000_000, 0.0) is None


def test_gap_pct_positive() -> None:
    g = gap_pct(Decimal("105"), Decimal("100"))
    assert g is not None and abs(g - 5.0) < 1e-9


def test_gap_pct_handles_zero_previous_close() -> None:
    assert gap_pct(Decimal("100"), Decimal("0")) is None


def test_change_pct_negative() -> None:
    c = change_pct(Decimal("95"), Decimal("100"))
    assert c is not None and abs(c + 5.0) < 1e-9


def test_momentum_simple_ratio() -> None:
    closes = [Decimal(str(p)) for p in range(100, 120)]
    m = momentum(closes, 5)
    assert m is not None and m > 0


def test_momentum_short_history_returns_none() -> None:
    assert momentum([Decimal("100"), Decimal("101")], 5) is None


def test_adv_mean_of_trailing_volumes() -> None:
    vols = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]
    a = adv(vols, sessions=14)
    # mean of last 14 = (2+...+15)/14 = 8.5
    assert a is not None and abs(a - 8.5) < 1e-9


def test_dollar_volume_and_distance_metrics() -> None:
    assert dollar_volume(Decimal("10"), 1_000) == 10_000.0
    h = distance_from_high(Decimal("80"), Decimal("100"))
    assert h is not None and abs(h - 20.0) < 1e-9
    lo = distance_from_low(Decimal("120"), Decimal("100"))
    assert lo is not None and abs(lo - 20.0) < 1e-9


def test_is_above_handles_none() -> None:
    assert is_above(Decimal("100"), None) is None
    assert is_above(Decimal("100"), Decimal("99")) is True
    assert is_above(Decimal("98"), Decimal("99")) is False
