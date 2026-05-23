"""Single declarative catalog of every screenable field.

Adding a field here automatically updates:
  * the ``/api/screener/fields/`` endpoint payload (filter editor in the UI)
  * stage-1 param mapping in ``pipeline.py``
  * stage-3 post-filtering in ``pipeline.py``
  * capability gating
  * result-column metadata (where the field renders, units)

Deferred fields (short interest, premarket volume) are present here but
gated off by capability — when the active ``ScreenerDataSource`` does
not advertise every capability they require, ``/fields/`` returns them
with ``available: false`` and ``requires: [...]`` so the UI can render
them disabled with a tooltip rather than hiding the future contract.
"""
from __future__ import annotations

from dataclasses import dataclass

from .capabilities import ScreenerCapability as C


GROUP_DESCRIPTIVE = "Descriptive"
GROUP_LIQUIDITY = "Liquidity & Volume"
GROUP_PERFORMANCE = "Performance"
GROUP_FUNDAMENTAL = "Fundamental"

KIND_RANGE = "range"
KIND_ENUM = "enum"
KIND_BOOL = "bool"

ASSET_CLASS_EQUITY = "equity"
ASSET_CLASS_ETF = "etf"

# Curated enum values. ``sector`` mirrors the 11 GICS sectors; ``exchange``
# is the US-listed shortlist; ``country`` is a small curated list. The
# ``industry`` list is large + slow-changing — we fetch it lazily from
# FMP and cache in Redis (handled in `views.py`); kept empty here so the
# field is still served.
SECTORS = (
    "Basic Materials",
    "Communication Services",
    "Consumer Cyclical",
    "Consumer Defensive",
    "Energy",
    "Financial Services",
    "Healthcare",
    "Industrials",
    "Real Estate",
    "Technology",
    "Utilities",
)

EXCHANGES = ("NASDAQ", "NYSE", "AMEX")

COUNTRIES = ("US", "CA", "GB", "IE", "BM")


@dataclass(frozen=True)
class ScreenerField:
    id: str
    label: str
    group: str
    kind: str
    unit: str
    capabilities: frozenset
    fmp_param: tuple[str, str] | None
    enrich_metric: str | None
    asset_classes: tuple[str, ...]
    enum_values: tuple[str, ...] = ()
    description: str = ""


FIELD_REGISTRY: dict[str, ScreenerField] = {
    # ---- Descriptive (stage 1 FMP-native or capability gate) ---------
    "exchange": ScreenerField(
        id="exchange",
        label="Exchange",
        group=GROUP_DESCRIPTIVE,
        kind=KIND_ENUM,
        unit="",
        capabilities=frozenset({C.BASIC_SCREEN}),
        fmp_param=("exchange", "exchange"),
        enrich_metric=None,
        asset_classes=("equity", "etf"),
        enum_values=EXCHANGES,
        description=(
            "Listing venue (NASDAQ / NYSE / AMEX). Pick one to restrict "
            "results to that exchange; leave blank for all US exchanges."
        ),
    ),
    "sector": ScreenerField(
        id="sector",
        label="Sector",
        group=GROUP_DESCRIPTIVE,
        kind=KIND_ENUM,
        unit="",
        capabilities=frozenset({C.BASIC_SCREEN}),
        fmp_param=("sector", "sector"),
        enrich_metric=None,
        asset_classes=("equity", "etf"),
        enum_values=SECTORS,
        description=(
            "GICS sector (one of 11 — Technology, Healthcare, …). "
            "Pick one to constrain results to that sector; leave blank "
            "to screen across all sectors."
        ),
    ),
    "industry": ScreenerField(
        id="industry",
        label="Industry",
        group=GROUP_DESCRIPTIVE,
        kind=KIND_ENUM,
        unit="",
        capabilities=frozenset({C.BASIC_SCREEN}),
        fmp_param=("industry", "industry"),
        enrich_metric=None,
        asset_classes=("equity", "etf"),
        enum_values=(),
        description=(
            "GICS industry — narrower than sector (e.g. Semiconductors "
            "within Technology). Constrains results to a single industry."
        ),
    ),
    "country": ScreenerField(
        id="country",
        label="Country",
        group=GROUP_DESCRIPTIVE,
        kind=KIND_ENUM,
        unit="",
        capabilities=frozenset({C.BASIC_SCREEN}),
        fmp_param=("country", "country"),
        enrich_metric=None,
        asset_classes=("equity", "etf"),
        enum_values=COUNTRIES,
        description=(
            "Country of incorporation (ISO-2 code — US, CA, GB, …). "
            "Filters to companies domiciled in that country."
        ),
    ),
    "market_cap": ScreenerField(
        id="market_cap",
        label="Market Cap",
        group=GROUP_DESCRIPTIVE,
        kind=KIND_RANGE,
        unit="usd",
        capabilities=frozenset({C.BASIC_SCREEN}),
        fmp_param=("marketCapMoreThan", "marketCapLowerThan"),
        enrich_metric=None,
        asset_classes=("equity", "etf"),
        description=(
            "Total market value of all outstanding shares, in USD. "
            "Set min ≥ 300M to exclude small-caps, ≥ 10B for large-caps. "
            "Higher min = larger, more liquid companies."
        ),
    ),
    "price": ScreenerField(
        id="price",
        label="Price",
        group=GROUP_DESCRIPTIVE,
        kind=KIND_RANGE,
        unit="usd",
        capabilities=frozenset({C.BASIC_SCREEN}),
        fmp_param=("priceMoreThan", "priceLowerThan"),
        enrich_metric=None,
        asset_classes=("equity", "etf"),
        description=(
            "Latest trade price per share, in USD. Set min ≥ $5 to "
            "filter out penny stocks; set max to cap nominal price."
        ),
    ),
    # ---- Liquidity & Volume ------------------------------------------
    "volume": ScreenerField(
        id="volume",
        label="Volume (today)",
        group=GROUP_LIQUIDITY,
        kind=KIND_RANGE,
        unit="shares",
        capabilities=frozenset({C.INTRADAY_QUOTE}),
        # Special-cased in pipeline: stage-1 receives a relaxed floor;
        # the exact volume.min is applied in stage 3 against live data.
        fmp_param=("volumeMoreThan", "volumeLowerThan"),
        enrich_metric="volume_today",
        asset_classes=("equity", "etf"),
        description=(
            "Today's traded volume in shares (live). Set min ≥ 1M to "
            "keep liquid names. Higher min = tighter spreads, easier "
            "fills."
        ),
    ),
    "adv_14d": ScreenerField(
        id="adv_14d",
        label="Avg Daily Volume (14d)",
        group=GROUP_LIQUIDITY,
        kind=KIND_RANGE,
        unit="shares",
        capabilities=frozenset({C.DAILY_BARS}),
        fmp_param=None,
        enrich_metric="adv_14d",
        asset_classes=("equity", "etf"),
        description=(
            "Mean daily share volume over the last 14 completed sessions. "
            "Use as a baseline liquidity filter — set min ≥ 500k to "
            "exclude thinly-traded names."
        ),
    ),
    "rvol": ScreenerField(
        id="rvol",
        label="Relative Volume",
        group=GROUP_LIQUIDITY,
        kind=KIND_RANGE,
        unit="ratio",
        capabilities=frozenset({C.INTRADAY_QUOTE, C.DAILY_BARS}),
        fmp_param=None,
        enrich_metric="rvol",
        asset_classes=("equity", "etf"),
        description=(
            "Today's volume ÷ 14-day ADV. 1.0× = normal, 2.0× = double "
            "average. Set min ≥ 2 to surface unusually active stocks "
            "(news, catalyst, breakout)."
        ),
    ),
    "dollar_volume": ScreenerField(
        id="dollar_volume",
        label="Dollar Volume (today)",
        group=GROUP_LIQUIDITY,
        kind=KIND_RANGE,
        unit="usd",
        capabilities=frozenset({C.INTRADAY_QUOTE}),
        fmp_param=None,
        enrich_metric="dollar_volume",
        asset_classes=("equity", "etf"),
        description=(
            "Today's traded value in USD (price × volume). Best single "
            "measure of liquidity. Set min ≥ $20M for institutional-"
            "tradeable names."
        ),
    ),
    # ---- Performance --------------------------------------------------
    "gap_pct": ScreenerField(
        id="gap_pct",
        label="Gap %",
        group=GROUP_PERFORMANCE,
        kind=KIND_RANGE,
        unit="pct",
        capabilities=frozenset({C.INTRADAY_QUOTE}),
        fmp_param=None,
        enrich_metric="gap_pct",
        asset_classes=("equity", "etf"),
        description=(
            "Today's open vs yesterday's close, in percent. Positive = "
            "gap up (e.g. +4 = opened 4% higher), negative = gap down. "
            "Surfaces overnight catalysts."
        ),
    ),
    "change_pct": ScreenerField(
        id="change_pct",
        label="Day Change %",
        group=GROUP_PERFORMANCE,
        kind=KIND_RANGE,
        unit="pct",
        capabilities=frozenset({C.INTRADAY_QUOTE}),
        fmp_param=None,
        enrich_metric="change_pct",
        asset_classes=("equity", "etf"),
        description=(
            "Today's % move vs yesterday's close. Includes the gap and "
            "intraday drift. Use to find today's biggest movers."
        ),
    ),
    "momentum_1m": ScreenerField(
        id="momentum_1m",
        label="1-Month Return",
        group=GROUP_PERFORMANCE,
        kind=KIND_RANGE,
        unit="pct",
        capabilities=frozenset({C.DAILY_BARS}),
        fmp_param=None,
        enrich_metric="momentum_1m",
        asset_classes=("equity", "etf"),
        description=(
            "Price return over the last ~21 trading sessions, in percent. "
            "Captures short-term trend."
        ),
    ),
    "momentum_3m": ScreenerField(
        id="momentum_3m",
        label="3-Month Return",
        group=GROUP_PERFORMANCE,
        kind=KIND_RANGE,
        unit="pct",
        capabilities=frozenset({C.DAILY_BARS}),
        fmp_param=None,
        enrich_metric="momentum_3m",
        asset_classes=("equity", "etf"),
        description=(
            "Price return over the last ~63 trading sessions, in percent. "
            "Classic momentum window — set min ≥ 20 for sustained "
            "uptrends."
        ),
    ),
    "momentum_6m": ScreenerField(
        id="momentum_6m",
        label="6-Month Return",
        group=GROUP_PERFORMANCE,
        kind=KIND_RANGE,
        unit="pct",
        capabilities=frozenset({C.DAILY_BARS}),
        fmp_param=None,
        enrich_metric="momentum_6m",
        asset_classes=("equity", "etf"),
        description=(
            "Price return over the last ~126 trading sessions, in percent. "
            "Medium-term trend signal."
        ),
    ),
    "above_50d_ma": ScreenerField(
        id="above_50d_ma",
        label="Above 50-day MA",
        group=GROUP_PERFORMANCE,
        kind=KIND_BOOL,
        unit="",
        capabilities=frozenset({C.INTRADAY_QUOTE}),
        fmp_param=None,
        enrich_metric="above_50d_ma",
        asset_classes=("equity", "etf"),
        description=(
            "Yes = current price > 50-day simple moving average (a "
            "short-term uptrend signal). No = below. Any = no filter."
        ),
    ),
    "above_200d_ma": ScreenerField(
        id="above_200d_ma",
        label="Above 200-day MA",
        group=GROUP_PERFORMANCE,
        kind=KIND_BOOL,
        unit="",
        capabilities=frozenset({C.INTRADAY_QUOTE}),
        fmp_param=None,
        enrich_metric="above_200d_ma",
        asset_classes=("equity", "etf"),
        description=(
            "Yes = current price > 200-day SMA (a long-term bull-trend "
            "signal). Combine with Above 50-day MA for a full uptrend."
        ),
    ),
    "dist_52w_high": ScreenerField(
        id="dist_52w_high",
        label="% Below 52-wk High",
        group=GROUP_PERFORMANCE,
        kind=KIND_RANGE,
        unit="pct",
        capabilities=frozenset({C.INTRADAY_QUOTE}),
        fmp_param=None,
        enrich_metric="dist_52w_high",
        asset_classes=("equity", "etf"),
        description=(
            "How far the latest price sits below the trailing 52-week "
            "high, in percent. 0 = at the high. Set max ≤ 5 to find "
            "stocks near breakout."
        ),
    ),
    "dist_52w_low": ScreenerField(
        id="dist_52w_low",
        label="% Above 52-wk Low",
        group=GROUP_PERFORMANCE,
        kind=KIND_RANGE,
        unit="pct",
        capabilities=frozenset({C.INTRADAY_QUOTE}),
        fmp_param=None,
        enrich_metric="dist_52w_low",
        asset_classes=("equity", "etf"),
        description=(
            "How far above the trailing 52-week low, in percent. "
            "Set max ≤ 10 to find stocks near their lows."
        ),
    ),
    "has_positive_catalyst": ScreenerField(
        id="has_positive_catalyst",
        label="Recent positive news",
        group=GROUP_PERFORMANCE,
        kind=KIND_BOOL,
        unit="",
        capabilities=frozenset({C.NEWS_CATALYST}),
        fmp_param=None,
        enrich_metric="has_positive_catalyst",
        asset_classes=("equity", "etf"),
        description=(
            "Yes = at least one high-materiality positive news item in "
            "the last 7 days. Surfaces names with a recent catalyst."
        ),
    ),
    # ---- Fundamental --------------------------------------------------
    "beta": ScreenerField(
        id="beta",
        label="Beta",
        group=GROUP_FUNDAMENTAL,
        kind=KIND_RANGE,
        unit="ratio",
        capabilities=frozenset({C.BASIC_SCREEN}),
        fmp_param=("betaMoreThan", "betaLowerThan"),
        enrich_metric=None,
        asset_classes=("equity", "etf"),
        description=(
            "Volatility vs the broad market. 1.0 = moves with the "
            "market, >1 = more volatile, <1 = less. Set max < 1 for "
            "defensive, min > 1.5 for high-beta names."
        ),
    ),
    "pe_ratio": ScreenerField(
        id="pe_ratio",
        label="P/E Ratio",
        group=GROUP_FUNDAMENTAL,
        kind=KIND_RANGE,
        unit="ratio",
        capabilities=frozenset({C.INTRADAY_QUOTE}),
        fmp_param=None,
        enrich_metric="pe_ratio",
        asset_classes=("equity",),
        description=(
            "Price ÷ trailing-twelve-month earnings per share. Lower = "
            "cheaper on earnings. Set max ≤ 15 for value, min ≥ 30 for "
            "growth-priced names. Negative = unprofitable."
        ),
    ),
    "eps": ScreenerField(
        id="eps",
        label="EPS",
        group=GROUP_FUNDAMENTAL,
        kind=KIND_RANGE,
        unit="usd",
        capabilities=frozenset({C.INTRADAY_QUOTE}),
        fmp_param=None,
        enrich_metric="eps",
        asset_classes=("equity",),
        description=(
            "Earnings per share over the last 12 months, in USD. "
            "Set min > 0 to exclude unprofitable companies."
        ),
    ),
    "dividend": ScreenerField(
        id="dividend",
        label="Dividend (annual)",
        group=GROUP_FUNDAMENTAL,
        kind=KIND_RANGE,
        unit="usd",
        capabilities=frozenset({C.BASIC_SCREEN}),
        fmp_param=("dividendMoreThan", "dividendLowerThan"),
        enrich_metric=None,
        asset_classes=("equity",),
        description=(
            "Last annual dividend per share, in USD. Set min > 0 to "
            "filter for dividend-payers."
        ),
    ),
    # ---- Deferred (defined, capability-gated off) --------------------
    "short_interest_pct": ScreenerField(
        id="short_interest_pct",
        label="Short % of Float",
        group=GROUP_FUNDAMENTAL,
        kind=KIND_RANGE,
        unit="pct",
        capabilities=frozenset({C.SHORT_INTEREST}),
        fmp_param=None,
        enrich_metric="short_interest_pct",
        asset_classes=("equity",),
        description=(
            "Percentage of public float sold short. High values "
            "(≥ 20%) signal squeeze candidates. Requires a "
            "short-interest data source — not yet available."
        ),
    ),
    "days_to_cover": ScreenerField(
        id="days_to_cover",
        label="Days to Cover",
        group=GROUP_FUNDAMENTAL,
        kind=KIND_RANGE,
        unit="ratio",
        capabilities=frozenset({C.SHORT_INTEREST}),
        fmp_param=None,
        enrich_metric="days_to_cover",
        asset_classes=("equity",),
        description=(
            "Short interest ÷ average daily volume — how many days for "
            "shorts to cover at average volume. Requires a "
            "short-interest data source — not yet available."
        ),
    ),
    "premarket_volume": ScreenerField(
        id="premarket_volume",
        label="Premarket Volume",
        group=GROUP_LIQUIDITY,
        kind=KIND_RANGE,
        unit="shares",
        capabilities=frozenset({C.PREMARKET_VOLUME}),
        fmp_param=None,
        enrich_metric="premarket_volume",
        asset_classes=("equity", "etf"),
        description=(
            "Shares traded in the premarket session (4:00–9:30 AM ET). "
            "Requires a premarket data source — not yet available."
        ),
    ),
    "premarket_vol_vs_adv": ScreenerField(
        id="premarket_vol_vs_adv",
        label="Premarket Vol ÷ 14d ADV",
        group=GROUP_LIQUIDITY,
        kind=KIND_RANGE,
        unit="ratio",
        capabilities=frozenset({C.PREMARKET_VOLUME, C.DAILY_BARS}),
        fmp_param=None,
        enrich_metric="premarket_vol_vs_adv",
        asset_classes=("equity", "etf"),
        description=(
            "Premarket volume as a multiple of 14-day ADV. High values "
            "(> 0.3) flag pre-open catalysts. Requires a premarket "
            "data source — not yet available."
        ),
    ),
}


# Map each capability to a stable string used in `requires`/serializer output.
CAPABILITY_LABEL: dict = {
    C.BASIC_SCREEN: "basic_screen",
    C.DAILY_BARS: "daily_bars",
    C.INTRADAY_QUOTE: "intraday_quote",
    C.NEWS_CATALYST: "news_catalyst",
    C.SHORT_INTEREST: "short_interest",
    C.PREMARKET_VOLUME: "premarket_volume",
}


def field_required_capabilities(field: ScreenerField) -> list[str]:
    """Stable, sorted list of capability labels required by a field."""
    return sorted(CAPABILITY_LABEL[c] for c in field.capabilities)


def field_missing_capabilities(
    field: ScreenerField, available: frozenset
) -> list[str]:
    """Subset of `field.capabilities` not satisfied by `available`."""
    return sorted(
        CAPABILITY_LABEL[c] for c in field.capabilities if c not in available
    )
