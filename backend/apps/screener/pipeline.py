"""Three-stage screening pipeline.

The market screener serves *today* data and must never be imported by a
backtest or point-in-time agent path. The ``tests/test_screener_pit.py``
regression test asserts the import boundary.

Stage 1 — coarse FMP ``/company-screener`` filter (one HTTP call).
Stage 2 — bounded enrichment: batch quotes + cached daily bars.
Stage 3 — post-filter, sort, limit.

Result caching (Redis, 90 s TTL) and a per-user 1-call-per-5-s rate
limit in the view layer keep FMP usage sane.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import math
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Iterable

from apps.data.cache import cache_get, cache_set
from apps.data.interfaces import Bar, QuoteSnapshot, ScreenerRow
from apps.data.models import DailyBar, NewsItem

from .capabilities import ScreenerCapability
from .datasource import ScreenerDataSource
from .fields import (
    CAPABILITY_LABEL,
    FIELD_REGISTRY,
    KIND_BOOL,
    KIND_ENUM,
    KIND_RANGE,
    ScreenerField,
)
from .metrics import (
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

log = logging.getLogger(__name__)


MAX_UNIVERSE = 3000
MAX_ENRICH = 300
RESULT_CACHE_TTL_SECONDS = 90
NEWS_CATALYST_LOOKBACK_DAYS = 7
NEWS_CATALYST_MIN_MATERIALITY = 0.6
POSITIVE_MATERIALITY_TAGS = {"positive", "bullish", "beat"}


@dataclass
class ScreenResultRow:
    ticker: str
    name: str
    exchange: str
    sector: str
    industry: str
    is_etf: bool
    price: Decimal | None
    change_pct: float | None
    gap_pct: float | None
    rvol: float | None
    volume: int | None
    adv_14d: float | None
    dollar_volume: float | None
    market_cap: Decimal | None
    pe_ratio: Decimal | None
    eps: Decimal | None
    beta: Decimal | None
    momentum_1m: float | None
    momentum_3m: float | None
    momentum_6m: float | None
    dist_52w_high: float | None
    dist_52w_low: float | None
    above_50d_ma: bool | None
    above_200d_ma: bool | None
    has_positive_catalyst: bool
    in_watchlist: bool
    warnings: list[str]


@dataclass
class ScreenResult:
    rows: list[ScreenResultRow]
    universe_size: int
    enriched_count: int
    returned_count: int
    truncated: bool
    as_of: dt.datetime
    provider: str
    capabilities: list[str]
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class ScreenerValidationError(ValueError):
    """Raised when a filter set fails validation against the registry."""


def validate_filters(
    filters: dict[str, Any], *, available_capabilities: frozenset
) -> dict[str, Any]:
    """Returns a normalized copy of ``filters`` or raises ``ScreenerValidationError``.

    A filter referencing a deferred capability the active data source
    does not advertise is rejected here so the pipeline never has to
    decide what to do with an impossible request.
    """
    if not isinstance(filters, dict):
        raise ScreenerValidationError("filters must be an object")

    asset_class = filters.get("asset_class", "equity")
    if asset_class not in {"equity", "etf", "all"}:
        raise ScreenerValidationError(
            f"asset_class must be one of equity / etf / all; got {asset_class!r}"
        )

    raw_criteria = filters.get("criteria") or {}
    if not isinstance(raw_criteria, dict):
        raise ScreenerValidationError("criteria must be an object")

    criteria: dict[str, Any] = {}
    for fid, spec in raw_criteria.items():
        if fid not in FIELD_REGISTRY:
            raise ScreenerValidationError(f"unknown field: {fid!r}")
        f = FIELD_REGISTRY[fid]
        missing = [
            CAPABILITY_LABEL[c]
            for c in f.capabilities
            if c not in available_capabilities
        ]
        if missing:
            raise ScreenerValidationError(
                f"field {fid!r} requires {missing} which the active data "
                f"source does not provide"
            )
        if not isinstance(spec, dict):
            raise ScreenerValidationError(
                f"criterion {fid!r} must be an object, got {type(spec).__name__}"
            )
        if f.kind == KIND_RANGE:
            mn = spec.get("min")
            mx = spec.get("max")
            if mn is None and mx is None:
                continue
            for label, val in (("min", mn), ("max", mx)):
                if val is None:
                    continue
                if not isinstance(val, (int, float)):
                    raise ScreenerValidationError(
                        f"criterion {fid!r}.{label} must be a number"
                    )
            if mn is not None and mx is not None and float(mn) > float(mx):
                raise ScreenerValidationError(
                    f"criterion {fid!r}: min ({mn}) > max ({mx})"
                )
            criteria[fid] = {
                k: v for k, v in (("min", mn), ("max", mx)) if v is not None
            }
        elif f.kind == KIND_ENUM:
            values = spec.get("values")
            if not isinstance(values, list) or not values:
                raise ScreenerValidationError(
                    f"criterion {fid!r}.values must be a non-empty list"
                )
            if any(not isinstance(v, str) or not v for v in values):
                raise ScreenerValidationError(
                    f"criterion {fid!r}.values must be a list of strings"
                )
            criteria[fid] = {"values": list(values)}
        elif f.kind == KIND_BOOL:
            value = spec.get("value")
            if not isinstance(value, bool):
                raise ScreenerValidationError(
                    f"criterion {fid!r}.value must be a boolean"
                )
            criteria[fid] = {"value": value}
        else:
            raise ScreenerValidationError(
                f"criterion {fid!r} has unsupported kind {f.kind}"
            )

    sort = filters.get("sort") or {"field": "market_cap", "dir": "desc"}
    if not isinstance(sort, dict):
        raise ScreenerValidationError("sort must be an object")
    sort_field = sort.get("field")
    sort_dir = sort.get("dir", "desc")
    if sort_field not in FIELD_REGISTRY:
        raise ScreenerValidationError(
            f"sort.field {sort_field!r} is not a known field"
        )
    if sort_dir not in {"asc", "desc"}:
        raise ScreenerValidationError(
            f"sort.dir must be asc or desc; got {sort_dir!r}"
        )

    limit = filters.get("limit", 200)
    if not isinstance(limit, int) or limit < 1 or limit > 1000:
        raise ScreenerValidationError("limit must be an int between 1 and 1000")

    return {
        "asset_class": asset_class,
        "criteria": criteria,
        "sort": {"field": sort_field, "dir": sort_dir},
        "limit": limit,
    }


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def _stage1_params(
    *, asset_class: str, criteria: dict[str, Any]
) -> dict[str, Any]:
    """Map registered range/enum criteria to FMP-native params for stage 1.

    Volume is special-cased: a relaxed floor is pushed to stage 1 (FMP's
    screener volume is an EOD coarse figure), and the exact volume.min is
    re-applied in stage 3 against live data.
    """
    params: dict[str, Any] = {"limit": MAX_UNIVERSE}
    if asset_class == "equity":
        params["isEtf"] = False
    elif asset_class == "etf":
        params["isEtf"] = True
    # "all" → don't constrain isEtf

    for fid, spec in criteria.items():
        f = FIELD_REGISTRY.get(fid)
        if f is None or f.fmp_param is None:
            continue
        more_key, less_key = f.fmp_param
        if f.kind == KIND_RANGE:
            mn = spec.get("min")
            mx = spec.get("max")
            if fid == "volume":
                # Relaxed floor only.
                if mn is not None:
                    relaxed = max(float(mn) * 0.25, 50_000.0)
                    params[more_key] = int(relaxed)
                # Stage-1 cap on absurd ceilings still pushed when present.
                if mx is not None:
                    params[less_key] = int(mx)
                continue
            if mn is not None:
                params[more_key] = mn
            if mx is not None:
                params[less_key] = mx
        elif f.kind == KIND_ENUM:
            values = spec.get("values") or []
            if values:
                # FMP accepts a single string for sector/industry/exchange/country.
                # We push the first value to stage 1 and re-filter in stage 3.
                # (Stage-3 filtering on enums is also handled below.)
                params[more_key] = values[0]
    return params


def _bar_closes(bars: list[Bar]) -> list[Decimal]:
    return [b.close for b in bars]


def _bar_volumes(bars: list[Bar]) -> list[int]:
    return [b.volume for b in bars]


def _enrich_row(
    row: ScreenerRow,
    *,
    ds: ScreenerDataSource,
    quote: QuoteSnapshot | None,
    today: dt.date,
    in_watchlist: bool,
    catalyst_tickers: set[str],
    capabilities: frozenset,
) -> ScreenResultRow:
    """Compute enrichment metrics for a single candidate row."""
    warnings: list[str] = []
    bars: list[Bar] = []
    if ScreenerCapability.DAILY_BARS in capabilities:
        try:
            bars = ds.daily_bars(row.ticker, end=today, lookback_days=380)
        except Exception as exc:  # noqa: BLE001 — provider can raise any HTTP error
            log.warning(
                "screener_enrich daily_bars_failed ticker=%s err=%s", row.ticker, exc
            )
            warnings.append("daily bars unavailable")

    closes = _bar_closes(bars)
    volumes = _bar_volumes(bars)

    # Quote-derived metrics (with EOD fallback to the last bar if no quote).
    eod_fallback = False
    if quote is None:
        if bars:
            last = bars[-1]
            eod_fallback = True
            quote = QuoteSnapshot(
                ticker=row.ticker,
                price=last.close,
                open=last.open,
                previous_close=bars[-2].close if len(bars) >= 2 else last.close,
                day_high=last.high,
                day_low=last.low,
                year_high=max((b.high for b in bars[-252:]), default=last.high),
                year_low=min((b.low for b in bars[-252:]), default=last.low),
                price_avg_50=Decimal(
                    str(sum(float(c) for c in closes[-50:]) / max(1, min(50, len(closes))))
                )
                if closes
                else None,
                price_avg_200=Decimal(
                    str(sum(float(c) for c in closes[-200:]) / max(1, min(200, len(closes))))
                )
                if closes
                else None,
                volume=last.volume,
                change_pct=None,
                market_cap=row.market_cap,
                pe_ratio=None,
                eps=None,
                as_of=today,
            )
            warnings.append("EOD fallback")
        else:
            warnings.append("no live quote and no bars")

    price = quote.price if quote else row.price
    volume_today = quote.volume if quote else None
    open_price = quote.open if quote else None
    prev_close = quote.previous_close if quote else None
    year_high = quote.year_high if quote else None
    year_low = quote.year_low if quote else None
    price_avg_50 = quote.price_avg_50 if quote else None
    price_avg_200 = quote.price_avg_200 if quote else None

    adv_14d = adv(volumes, sessions=14) if volumes else None
    rvol = relative_volume(volume_today, adv_14d)
    gap = gap_pct(open_price, prev_close)
    change = (
        change_pct(price, prev_close)
        if not eod_fallback
        else (float(quote.change_pct) if quote and quote.change_pct is not None else None)
    )
    if change is None and quote and quote.change_pct is not None:
        try:
            change = float(quote.change_pct)
        except (TypeError, ValueError):
            change = None

    mom_1m = momentum(closes, 21)
    mom_3m = momentum(closes, 63)
    mom_6m = momentum(closes, 126)

    dist_high = distance_from_high(price, year_high)
    dist_low = distance_from_low(price, year_low)

    above_50 = is_above(price, price_avg_50)
    above_200 = is_above(price, price_avg_200)

    dv = dollar_volume(price, volume_today)

    if rvol is None and ScreenerCapability.DAILY_BARS in capabilities and not bars:
        warnings.append("thin history — rvol unavailable")

    return ScreenResultRow(
        ticker=row.ticker,
        name=row.name,
        exchange=row.exchange,
        sector=row.sector,
        industry=row.industry,
        is_etf=row.is_etf,
        price=price,
        change_pct=change,
        gap_pct=gap,
        rvol=rvol,
        volume=volume_today,
        adv_14d=adv_14d,
        dollar_volume=dv,
        market_cap=quote.market_cap if quote and quote.market_cap else row.market_cap,
        pe_ratio=quote.pe_ratio if quote else None,
        eps=quote.eps if quote else None,
        beta=row.beta,
        momentum_1m=mom_1m,
        momentum_3m=mom_3m,
        momentum_6m=mom_6m,
        dist_52w_high=dist_high,
        dist_52w_low=dist_low,
        above_50d_ma=above_50,
        above_200d_ma=above_200,
        has_positive_catalyst=row.ticker.upper() in catalyst_tickers,
        in_watchlist=in_watchlist,
        warnings=warnings,
    )


def _passes_criterion(row: ScreenResultRow, fid: str, spec: dict[str, Any]) -> bool:
    """Stage-3 post-filter check for a single criterion against an enriched row."""
    f = FIELD_REGISTRY.get(fid)
    if f is None:
        return True
    if f.kind == KIND_RANGE:
        val = _row_value(row, fid)
        if val is None:
            # Missing values fail any explicit min/max — opt-in semantics.
            return False
        try:
            v = float(val)
        except (TypeError, ValueError):
            return False
        mn = spec.get("min")
        mx = spec.get("max")
        if mn is not None and v < float(mn):
            return False
        if mx is not None and v > float(mx):
            return False
        return True
    if f.kind == KIND_BOOL:
        val = _row_value(row, fid)
        return bool(val) is bool(spec.get("value"))
    if f.kind == KIND_ENUM:
        val = _row_value(row, fid)
        return str(val) in {str(v) for v in spec.get("values", [])}
    return True


def _row_value(row: ScreenResultRow, fid: str) -> Any:
    """Map a registered field id to the corresponding attribute on a result row."""
    mapping = {
        "exchange": row.exchange,
        "sector": row.sector,
        "industry": row.industry,
        "country": "",  # not retained on row; stage-1-only enum
        "market_cap": row.market_cap,
        "price": row.price,
        "volume": row.volume,
        "adv_14d": row.adv_14d,
        "rvol": row.rvol,
        "dollar_volume": row.dollar_volume,
        "gap_pct": row.gap_pct,
        "change_pct": row.change_pct,
        "momentum_1m": row.momentum_1m,
        "momentum_3m": row.momentum_3m,
        "momentum_6m": row.momentum_6m,
        "above_50d_ma": row.above_50d_ma,
        "above_200d_ma": row.above_200d_ma,
        "dist_52w_high": row.dist_52w_high,
        "dist_52w_low": row.dist_52w_low,
        "beta": row.beta,
        "pe_ratio": row.pe_ratio,
        "eps": row.eps,
        "has_positive_catalyst": row.has_positive_catalyst,
    }
    return mapping.get(fid)


def _sort_key(row: ScreenResultRow, field_id: str) -> tuple[int, float]:
    """Stable sort key with ``None`` always at the back."""
    val = _row_value(row, field_id)
    if val is None:
        return (1, 0.0)
    try:
        return (0, float(val))
    except (TypeError, ValueError):
        return (0, 0.0)


def _catalyst_tickers(tickers: list[str], today: dt.date) -> set[str]:
    """Pre-fetch the set of tickers with a recent positive high-materiality NewsItem."""
    if not tickers:
        return set()
    cutoff = dt.datetime.combine(
        today - dt.timedelta(days=NEWS_CATALYST_LOOKBACK_DAYS),
        dt.time(0, 0),
        tzinfo=dt.UTC,
    )
    qs = NewsItem.objects.filter(
        ticker__in=[t.upper() for t in tickers],
        published_at__gte=cutoff,
        materiality_score__gte=NEWS_CATALYST_MIN_MATERIALITY,
    )
    out: set[str] = set()
    for ni in qs.only("ticker", "materiality_tag", "materiality_score"):
        tag = (ni.materiality_tag or "").lower()
        if tag in POSITIVE_MATERIALITY_TAGS or not tag:
            out.add(ni.ticker.upper())
    return out


def _normalized_cache_key(filters: dict[str, Any], user_id: int | None) -> str:
    """Deterministic cache key per (user, normalized filters)."""
    encoded = json.dumps(filters, sort_keys=True, default=str)
    digest = hashlib.sha1(encoded.encode("utf-8")).hexdigest()
    return f"screener:run:{user_id or 0}:{digest}"


def _result_to_dict(result: ScreenResult) -> dict[str, Any]:
    d = asdict(result)
    d["as_of"] = result.as_of.isoformat()
    return d


def _result_from_dict(data: dict[str, Any]) -> ScreenResult:
    rows = [ScreenResultRow(**r) for r in data.get("rows", [])]
    return ScreenResult(
        rows=rows,
        universe_size=int(data.get("universe_size", 0)),
        enriched_count=int(data.get("enriched_count", 0)),
        returned_count=int(data.get("returned_count", 0)),
        truncated=bool(data.get("truncated", False)),
        as_of=dt.datetime.fromisoformat(data["as_of"]),
        provider=str(data.get("provider", "")),
        capabilities=list(data.get("capabilities", [])),
        warnings=list(data.get("warnings", [])),
    )


def run_screen(
    filters: dict[str, Any],
    *,
    user: Any,
    datasource: ScreenerDataSource,
    watchlist_tickers: set[str] | None = None,
    use_cache: bool = True,
) -> ScreenResult:
    """Execute the three-stage screening pipeline.

    ``filters`` MUST be the normalized shape produced by ``validate_filters``.
    Caller (the view layer) handles validation and the per-user rate limit
    so this function is exercise-able in isolation from tests.
    """
    today = dt.date.today()
    user_id = int(getattr(user, "id", 0) or 0) if user is not None else None
    watchlist_tickers = watchlist_tickers or set()

    capabilities = datasource.capabilities()
    cap_labels = sorted(CAPABILITY_LABEL[c] for c in capabilities)

    cache_key = _normalized_cache_key(filters, user_id)
    if use_cache:
        cached = cache_get(cache_key)
        if cached:
            try:
                return _result_from_dict(cached)
            except (KeyError, ValueError, TypeError):
                # Bad cache entry — fall through to a fresh run.
                pass

    asset_class = filters["asset_class"]
    criteria: dict[str, Any] = filters["criteria"]
    sort = filters["sort"]
    limit = filters["limit"]

    warnings: list[str] = []

    # --------- STAGE 1 ---------
    stage1_params = _stage1_params(asset_class=asset_class, criteria=criteria)
    try:
        candidates = datasource.screen(stage1_params)
    except Exception as exc:  # noqa: BLE001 — provider may raise httpx.HTTPStatusError
        msg = str(exc)
        if "402" in msg or "403" in msg or "Forbidden" in msg or "Payment" in msg:
            raise RuntimeError(
                "Your FMP plan does not include the Stock Screener endpoint. "
                "Upgrade your plan or set a different key at /settings/models."
            ) from exc
        raise

    universe_size = len(candidates)
    truncated = False
    if universe_size > MAX_ENRICH:
        truncated = True
        warnings.append(
            f"Showing the {MAX_ENRICH} most liquid matches — "
            "tighten your filters for full coverage."
        )
        # Pre-rank by stage-1 dollar volume (price × volume, both already
        # known) — cheapest available liquidity proxy.
        candidates = sorted(
            candidates,
            key=lambda r: (
                float(r.price or 0) * float(r.volume or 0)
            ),
            reverse=True,
        )[:MAX_ENRICH]

    # --------- STAGE 2 ---------
    tickers = [c.ticker for c in candidates]
    quotes: dict[str, QuoteSnapshot] = {}
    if tickers and ScreenerCapability.INTRADAY_QUOTE in capabilities:
        try:
            quotes = datasource.quotes(tickers)
        except Exception as exc:  # noqa: BLE001
            log.warning("screener_stage2 quotes_failed err=%s", exc)
            warnings.append(
                "Intraday quotes unavailable — showing end-of-day data. "
                "A premium FMP plan is required for live screening."
            )
            quotes = {}

    if not quotes and tickers and ScreenerCapability.INTRADAY_QUOTE in capabilities:
        warnings.append(
            "Intraday quotes unavailable — showing end-of-day data. "
            "A premium FMP plan is required for live screening."
        )

    catalyst_tickers = (
        _catalyst_tickers(tickers, today)
        if ScreenerCapability.NEWS_CATALYST in capabilities
        else set()
    )

    enriched: list[ScreenResultRow] = []
    for cand in candidates:
        try:
            enriched.append(
                _enrich_row(
                    cand,
                    ds=datasource,
                    quote=quotes.get(cand.ticker.upper()),
                    today=today,
                    in_watchlist=cand.ticker.upper() in watchlist_tickers,
                    catalyst_tickers=catalyst_tickers,
                    capabilities=capabilities,
                )
            )
        except Exception as exc:  # noqa: BLE001 — never let one row break the screen
            log.warning(
                "screener_enrich_row_failed ticker=%s err=%s", cand.ticker, exc
            )

    # --------- STAGE 3 ---------
    survivors: list[ScreenResultRow] = []
    for row in enriched:
        ok = True
        for fid, spec in criteria.items():
            f = FIELD_REGISTRY.get(fid)
            if f is None:
                continue
            # Stage-1-only enums (country) are not re-evaluated post-enrichment.
            if f.kind == KIND_ENUM and f.fmp_param is not None:
                values = spec.get("values") or []
                if not values:
                    continue
                attr = _row_value(row, fid)
                if attr is not None and attr != "" and attr not in values:
                    ok = False
                    break
                continue
            if not _passes_criterion(row, fid, spec):
                ok = False
                break
        if ok:
            survivors.append(row)

    reverse = sort["dir"] == "desc"
    survivors.sort(key=lambda r: _sort_key(r, sort["field"]), reverse=reverse)
    final = survivors[:limit]

    result = ScreenResult(
        rows=final,
        universe_size=universe_size,
        enriched_count=len(enriched),
        returned_count=len(final),
        truncated=truncated,
        as_of=dt.datetime.now(tz=dt.UTC),
        provider=datasource.provider_name,
        capabilities=cap_labels,
        warnings=warnings,
    )

    if use_cache:
        cache_set(
            cache_key, _result_to_dict(result), ttl_seconds=RESULT_CACHE_TTL_SECONDS
        )
    return result


def enrich_watchlist(
    tickers: Iterable[str],
    *,
    user: Any,
    datasource: ScreenerDataSource,
) -> list[ScreenResultRow]:
    """Reuse the enrichment path to populate the Watchlist view.

    Skips the stage-1 ``/company-screener`` call: builds a thin
    ``ScreenerRow`` per ticker straight from the live quote (and any
    cached bars / NewsItem), so the panel shows live price / change /
    RVOL without a second pipeline pass.
    """
    today = dt.date.today()
    tickers_list = [t.upper() for t in tickers if t]
    capabilities = datasource.capabilities()

    quotes: dict[str, QuoteSnapshot] = {}
    if tickers_list and ScreenerCapability.INTRADAY_QUOTE in capabilities:
        try:
            quotes = datasource.quotes(tickers_list)
        except Exception as exc:  # noqa: BLE001
            log.warning("watchlist_enrich quotes_failed err=%s", exc)

    catalyst_tickers = (
        _catalyst_tickers(tickers_list, today)
        if ScreenerCapability.NEWS_CATALYST in capabilities
        else set()
    )

    rows: list[ScreenResultRow] = []
    for tk in tickers_list:
        quote = quotes.get(tk)
        thin = ScreenerRow(
            ticker=tk,
            name="",
            market_cap=quote.market_cap if quote else None,
            price=quote.price if quote else None,
            volume=quote.volume if quote else None,
            beta=None,
            sector="",
            industry="",
            exchange="",
            country="",
            is_etf=False,
            is_fund=False,
            last_annual_dividend=None,
        )
        try:
            rows.append(
                _enrich_row(
                    thin,
                    ds=datasource,
                    quote=quote,
                    today=today,
                    in_watchlist=True,
                    catalyst_tickers=catalyst_tickers,
                    capabilities=capabilities,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("watchlist_enrich_row_failed ticker=%s err=%s", tk, exc)
    return rows
