"""WP B4 — unit contract for the screener field registry.

Every ``ScreenerField`` that is produced by ``_enrich_row`` declares a
``unit``. The filter editor renders that unit as the suffix a user types
against, so the declared unit MUST match the scale the pipeline actually
produces:

  * ``pct``   — percentage points (a +4% move is ``4.0``)
  * ``ratio`` — a plain ratio/multiple (a +4% move is ``0.04``)

The momentum fields used to declare ``pct`` while ``metrics.momentum``
returned a ratio, and their descriptions told the user to "set min >= 20"
— which admits only a +2000% mover, so following the instruction returned
nothing. They now declare ``ratio`` (matching the metric, the preset and
the UI's own ``formatPct`` which multiplies by 100).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.data.interfaces import Bar, QuoteSnapshot, ScreenerRow
from apps.screener.capabilities import ScreenerCapability
from apps.screener.fields import FIELD_REGISTRY, KIND_RANGE
from apps.screener.pipeline import _enrich_row

# What each enriched RANGE field must be, given a fixture engineered so every
# move is exactly +4%. "pct" fields must read 4.0; "ratio" fields must read
# 0.04. Fields whose scale is not a return are listed with their own unit.
EXPECTED_UNITS = {
    "change_pct": "pct",
    "gap_pct": "pct",
    "rvol": "ratio",
    "volume": "shares",
    "adv_14d": "shares",
    "dollar_volume": "usd",
    "pe_ratio": "ratio",
    "eps": "usd",
    "momentum_1m": "ratio",
    "momentum_3m": "ratio",
    "momentum_6m": "ratio",
    "dist_52w_high": "pct",
    "dist_52w_low": "pct",
    # Capability-gated / not produced by `_enrich_row` yet (short-interest and
    # premarket feeds); listed so a new field still forces a table update.
    "days_to_cover": "ratio",
    "premarket_vol_vs_adv": "ratio",
    "premarket_volume": "shares",
    "short_interest_pct": "pct",
}

_CAPS = frozenset(
    {
        ScreenerCapability.BASIC_SCREEN,
        ScreenerCapability.DAILY_BARS,
        ScreenerCapability.INTRADAY_QUOTE,
    }
)


class _DS:
    """Bars that rise exactly 4% over each momentum window, and 4% off a
    52-week high / above a 52-week low."""

    provider_name = "fixture"

    def capabilities(self):
        return _CAPS

    def daily_bars(self, ticker, *, end, lookback_days):
        # 300 sessions: flat at 100 until the last 127, then a single step so
        # that the 21/63/126-session lookbacks all read exactly +4%.
        out: list[Bar] = []
        d = dt.date(2026, 1, 1)
        for i in range(300):
            px = Decimal("100") if i < 300 - 21 else Decimal("104")
            out.append(
                Bar(ticker=ticker, date=d + dt.timedelta(days=i), open=px, high=px,
                    low=px, close=px, adjusted_close=px, volume=1_000_000)
            )
        return out


def _row() -> ScreenerRow:
    return ScreenerRow(
        ticker="AAA", name="AAA", market_cap=Decimal("1e9"), price=Decimal("104"),
        volume=1_000_000, beta=Decimal("1.1"), sector="Technology", industry="",
        exchange="NASDAQ", country="US", is_etf=False, is_fund=False,
        last_annual_dividend=None,
    )


def _quote() -> QuoteSnapshot:
    return QuoteSnapshot(
        ticker="AAA", price=Decimal("104"), open=Decimal("104"),
        previous_close=Decimal("100"), day_high=Decimal("104"), day_low=Decimal("104"),
        year_high=Decimal("104"), year_low=Decimal("100"),
        price_avg_50=Decimal("100"), price_avg_200=Decimal("100"), volume=1_000_000,
        change_pct=None, market_cap=Decimal("1e9"), pe_ratio=Decimal("20"),
        eps=Decimal("5"), as_of=dt.date(2026, 9, 7),
    )


def _enriched():
    return _enrich_row(
        _row(), ds=_DS(), quote=_quote(), today=dt.date(2026, 9, 7),
        in_watchlist=False, catalyst_tickers=set(), capabilities=_CAPS,
    )


def test_expected_units_table_covers_every_enriched_range_field():
    enriched_range_fields = {
        fid
        for fid, f in FIELD_REGISTRY.items()
        if f.kind == KIND_RANGE and f.enrich_metric
    }
    assert enriched_range_fields == set(EXPECTED_UNITS), (
        "a new enriched range field needs an entry in EXPECTED_UNITS"
    )


@pytest.mark.parametrize("fid,unit", sorted(EXPECTED_UNITS.items()))
def test_declared_unit_matches_the_table(fid, unit):
    assert FIELD_REGISTRY[fid].unit == unit


def test_enriched_values_are_on_the_scale_their_unit_declares():
    r = _enriched()
    # Same +4% move, two different declared units, two different magnitudes.
    assert r.gap_pct == pytest.approx(4.0)          # unit="pct"
    assert r.change_pct == pytest.approx(4.0)       # unit="pct"
    assert r.dist_52w_high == pytest.approx(0.0)    # unit="pct"
    assert r.dist_52w_low == pytest.approx(4.0)     # unit="pct"
    for fid in ("momentum_1m", "momentum_3m", "momentum_6m"):
        assert getattr(r, fid) == pytest.approx(0.04), fid  # unit="ratio"


def test_momentum_descriptions_do_not_tell_the_user_to_use_percent():
    for fid in ("momentum_1m", "momentum_3m", "momentum_6m"):
        f = FIELD_REGISTRY[fid]
        assert f.unit == "ratio"
        assert "in percent" not in f.description.lower(), fid
        assert "RATIO" in f.description, fid
    # The shipped preset is on the same scale as the metric: +20% == 0.20.
    from apps.screener.presets import PRESETS

    preset = next(p for p in PRESETS if p.id == "momentum")
    assert preset.filters["momentum_3m"] == {"min": 0.20}
    from apps.screener.pipeline import ScreenResultRow, _passes_criterion

    row = ScreenResultRow(
        ticker="UP", name="UP", exchange="NYSE", sector="Technology", industry="",
        is_etf=False, price=Decimal("10"), change_pct=None, gap_pct=None, rvol=None,
        volume=None, adv_14d=None, dollar_volume=None, market_cap=None, pe_ratio=None,
        eps=None, beta=None, momentum_1m=None, momentum_3m=0.25, momentum_6m=None,
        dist_52w_high=None, dist_52w_low=None, above_50d_ma=None, above_200d_ma=None,
        has_positive_catalyst=False, in_watchlist=False, warnings=[],
    )
    # A +25% mover now passes the preset that claims to want ">= +20%".
    assert _passes_criterion(row, "momentum_3m", preset.filters["momentum_3m"]) is True
