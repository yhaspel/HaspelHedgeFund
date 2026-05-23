"""Predefined market screens.

A preset is a named filter set + sort + the capability set it requires.
The ``/api/screener/presets/`` endpoint computes ``available`` per
preset by intersecting ``capabilities`` with what the active
``ScreenerDataSource`` advertises — so deferred presets (Short Squeeze)
show up as disabled chips with an actionable tooltip rather than as a
broken Run.

Customizing a preset is intentionally not a special-cased flow: clicking
"Customize" loads the preset's filter set into the editor, where every
piece is editable. There is no logic in a preset that the filter editor
cannot express.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .capabilities import ScreenerCapability as C
from .fields import CAPABILITY_LABEL


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    description: str
    asset_class: str
    filters: dict[str, Any]
    sort: dict[str, str]
    limit: int
    capabilities: frozenset


PRESETS: list[Preset] = [
    Preset(
        id="momentum",
        name="Momentum Stocks",
        description=(
            "Liquid names in a sustained uptrend: 3-month return ≥ +20%, "
            "above both the 50- and 200-day moving averages."
        ),
        asset_class="equity",
        filters={
            "market_cap": {"min": 300_000_000},
            "price": {"min": 5},
            "adv_14d": {"min": 500_000},
            "momentum_3m": {"min": 0.20},
            "above_50d_ma": {"value": True},
            "above_200d_ma": {"value": True},
        },
        sort={"field": "momentum_3m", "dir": "desc"},
        limit=200,
        capabilities=frozenset({C.BASIC_SCREEN, C.DAILY_BARS, C.INTRADAY_QUOTE}),
    ),
    Preset(
        id="gap",
        name="Gap Up",
        description=(
            "Stocks that opened ≥ 4% above the prior close. Switch the "
            "preset to Gap Down to flip the sign."
        ),
        asset_class="equity",
        filters={
            "price": {"min": 2},
            "adv_14d": {"min": 300_000},
            "gap_pct": {"min": 4},
        },
        sort={"field": "gap_pct", "dir": "desc"},
        limit=200,
        capabilities=frozenset({C.BASIC_SCREEN, C.DAILY_BARS, C.INTRADAY_QUOTE}),
    ),
    Preset(
        id="gap_down",
        name="Gap Down",
        description="Stocks that opened ≥ 4% below the prior close.",
        asset_class="equity",
        filters={
            "price": {"min": 2},
            "adv_14d": {"min": 300_000},
            "gap_pct": {"max": -4},
        },
        sort={"field": "gap_pct", "dir": "asc"},
        limit=200,
        capabilities=frozenset({C.BASIC_SCREEN, C.DAILY_BARS, C.INTRADAY_QUOTE}),
    ),
    Preset(
        id="large_caps",
        name="Large Caps",
        description=(
            "Market cap ≥ $10B. Includes ETFs — the simplest, cheapest "
            "screen (pure stage-1; no enrichment needed for the filter)."
        ),
        asset_class="all",
        filters={"market_cap": {"min": 10_000_000_000}},
        sort={"field": "market_cap", "dir": "desc"},
        limit=200,
        capabilities=frozenset({C.BASIC_SCREEN}),
    ),
    Preset(
        id="stocks_in_play",
        name="Stocks in Play",
        description=(
            "Unusually active names worth watching: volume ≥ 1M and "
            "RVOL ≥ 2 (today's volume vs the 14-day average). "
            "Premarket-volume filtering will be added when a premarket "
            "data source is available."
        ),
        asset_class="equity",
        filters={
            "price": {"min": 5},
            "volume": {"min": 1_000_000},
            "rvol": {"min": 2.0},
        },
        sort={"field": "rvol", "dir": "desc"},
        limit=200,
        capabilities=frozenset({C.BASIC_SCREEN, C.DAILY_BARS, C.INTRADAY_QUOTE}),
    ),
]
# Note: a Short Squeeze preset is intentionally not shipped — it would
# require a `SHORT_INTEREST` capability the current FMP-only data source
# does not provide. The deferred `short_interest_pct` / `days_to_cover`
# fields in `FIELD_REGISTRY` keep the capability seam intact so a future
# short-interest provider can ship a new preset additively.


def preset_missing_capabilities(preset: Preset, available: frozenset) -> list[str]:
    return sorted(
        CAPABILITY_LABEL[c] for c in preset.capabilities if c not in available
    )


def get_preset(preset_id: str) -> Preset | None:
    for p in PRESETS:
        if p.id == preset_id:
            return p
    return None
