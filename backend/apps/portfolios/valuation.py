"""P3: Mark-to-market valuation for the Manual Book.

``value_portfolio`` is computed on read; no Celery task is required.
The mark source is **cadence-aware**: ``daily`` reads the last close from
``FmpProvider.get_daily_bars``; ``delayed`` / ``manual`` read the latest
intraday quote from ``FmpProvider.get_latest_quote`` (premium FMP plan).
``apps/data/cache.py`` keys differ per cadence so users can't accidentally
share a stale daily close into intraday mode.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from datetime import date as date_cls
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.db.models import Sum
from django.utils import timezone

from apps.data.cache import cache_get, cache_set
from apps.data.models import CorporateAction
from apps.data.providers.factory import get_fmp_provider

from .models import LedgerEntry, Portfolio, PortfolioPreferences

# Daily-close marks rarely change inside a page render; 10 minutes is the
# rule of thumb from the phase plan.
MARK_CACHE_TTL_SECONDS = 600
# Bars older than this surface a "stale price" warning so the UI can badge it.
STALE_MARK_AFTER_DAYS = 4
# Look back this many calendar days to find the latest daily close. Generous
# to cover long weekends + holidays without missing a recent close.
LOOKBACK_DAYS = 14


@dataclass
class Mark:
    ticker: str
    price: Decimal
    as_of: date_cls
    age_days: int                   # whole days for the UI "stale" badge
    stale: bool
    source: str = "fmp"
    as_of_datetime: datetime | None = None  # populated by intraday quotes


@dataclass
class PositionValuation:
    id: int
    ticker: str
    quantity: Decimal
    avg_cost: Decimal
    is_short: bool
    sector: str
    opened_at: Any
    opened_via: str
    source_run_id: int | None
    source_decision_id: int | None
    note: str
    realized_pnl: Decimal
    mark_price: Decimal | None
    mark_as_of: date_cls | None
    mark_stale: bool
    market_value: Decimal
    unrealized_pnl: Decimal
    unrealized_pnl_pct: Decimal | None
    weight_pct: Decimal
    warnings: list[str] = field(default_factory=list)


@dataclass
class PreferencesView:
    """Frontend-facing view of PortfolioPreferences."""

    mark_cadence: str
    interval_minutes: int
    last_refreshed_at: datetime | None


@dataclass
class PortfolioValuation:
    portfolio_id: int
    name: str
    kind: str
    cash_balance: Decimal
    reserved_short_proceeds: Decimal
    free_cash: Decimal
    total_value: Decimal
    long_market_value: Decimal
    short_market_value: Decimal
    gross_exposure_pct: Decimal
    net_exposure_pct: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    positions: list[PositionValuation]
    preferences: PreferencesView | None = None
    warnings: list[str] = field(default_factory=list)


def _money(x: Decimal | float | int) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def get_or_create_preferences(user: Any) -> PortfolioPreferences:
    """Lazy-create the per-user portfolio preferences row with defaults."""
    prefs, _ = PortfolioPreferences.objects.get_or_create(user=user)
    return prefs


def _cache_key_for(cadence: str, ticker: str, as_of: date_cls) -> str:
    """Cache key isolated per cadence so daily and intraday don't collide."""
    if cadence == PortfolioPreferences.CADENCE_DAILY:
        return f"mark:fmp:daily:{ticker.upper()}:{as_of.isoformat()}"
    return f"mark:fmp:{cadence}:{ticker.upper()}"


def invalidate_mark_cache(user: Any, tickers: list[str]) -> None:
    """Drop cached marks for the given tickers across all cadences.

    Called by ``POST /api/portfolio/refresh-marks/`` so the next read
    re-queries FMP. We delete every cadence variant to avoid serving a
    stale value if the user switched mode between reads.
    """
    from apps.data.cache import _redis  # local import for tests

    today = timezone.now().date()
    try:
        client = _redis()
    except Exception:
        return
    for ticker in tickers:
        for key in (
            f"mark:fmp:daily:{ticker.upper()}:{today.isoformat()}",
            f"mark:fmp:delayed:{ticker.upper()}",
            f"mark:fmp:manual:{ticker.upper()}",
        ):
            try:
                client.delete(key)
            except Exception:
                pass


def _pct(x: Decimal) -> Decimal:
    return (x * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def get_mark(
    ticker: str,
    on: date_cls | None = None,
    *,
    user: Any,
    preferences: PortfolioPreferences | None = None,
) -> Mark | None:
    """Return the latest mark for ``ticker``, cadence-aware.

    Mode resolution:
      - ``daily``   → last close from ``get_daily_bars``; cache TTL 10 min;
                      stale > ``STALE_MARK_AFTER_DAYS`` calendar days.
      - ``delayed`` → intraday quote from ``get_latest_quote``; cache TTL
                      = ``interval_minutes * 60``; stale > 1.5× interval.
      - ``manual``  → same intraday source; cache TTL = ``interval_minutes
                      * 60``. The cache is invalidated by
                      ``invalidate_mark_cache`` on a manual refresh.

    Returns ``None`` if the provider has no data (unknown ticker).
    Raises the actionable ``RuntimeError`` from the provider factory when
    the user has no FMP key and platform fallback is disabled.
    """
    as_of = on or timezone.now().date()
    prefs = preferences or get_or_create_preferences(user)
    cadence = prefs.mark_cadence
    cache_key = _cache_key_for(cadence, ticker, as_of)
    cached = cache_get(cache_key)
    if cached:
        return _hydrate_cached_mark(cached)

    if cadence == PortfolioPreferences.CADENCE_DAILY:
        mark = _fetch_daily_mark(ticker, as_of, user=user)
        ttl = MARK_CACHE_TTL_SECONDS
    else:
        mark = _fetch_intraday_mark(ticker, as_of, user=user, prefs=prefs)
        ttl = max(prefs.interval_minutes, PortfolioPreferences.MIN_INTERVAL_MINUTES) * 60

    if mark is None:
        return None

    cache_set(
        cache_key,
        {
            "ticker": mark.ticker,
            "price": str(mark.price),
            "as_of": mark.as_of.isoformat(),
            "age_days": mark.age_days,
            "stale": mark.stale,
            "source": mark.source,
            "as_of_datetime": mark.as_of_datetime.isoformat()
            if mark.as_of_datetime
            else None,
        },
        ttl_seconds=ttl,
    )
    return mark


def _hydrate_cached_mark(cached: dict) -> Mark:
    as_of_dt = cached.get("as_of_datetime")
    return Mark(
        ticker=cached["ticker"],
        price=Decimal(cached["price"]),
        as_of=date_cls.fromisoformat(cached["as_of"]),
        age_days=int(cached["age_days"]),
        stale=bool(cached["stale"]),
        source=cached.get("source", "fmp"),
        as_of_datetime=datetime.fromisoformat(as_of_dt) if as_of_dt else None,
    )


def _fetch_daily_mark(ticker: str, as_of: date_cls, *, user: Any) -> Mark | None:
    provider = get_fmp_provider(user=user)
    start = as_of - timedelta(days=LOOKBACK_DAYS)
    bars = provider.get_daily_bars(ticker, start, as_of, as_of=as_of)
    if not bars:
        return None
    last = bars[-1]
    age_days = (as_of - last.date).days
    return Mark(
        ticker=ticker,
        price=Decimal(str(last.close)),
        as_of=last.date,
        age_days=age_days,
        stale=age_days > STALE_MARK_AFTER_DAYS,
    )


def _fetch_intraday_mark(
    ticker: str,
    as_of: date_cls,
    *,
    user: Any,
    prefs: PortfolioPreferences,
) -> Mark | None:
    """Try the intraday quote first; fall back to the daily close so an
    after-hours / weekend / off-market read still yields a number."""
    provider = get_fmp_provider(user=user)
    try:
        quote = provider.get_latest_quote(ticker)
    except Exception:  # premium endpoint may 403 — degrade to daily.
        quote = None
    if quote is None:
        return _fetch_daily_mark(ticker, as_of, user=user)

    price, as_of_dt = quote
    now = timezone.now()
    if as_of_dt.tzinfo is None:
        as_of_dt = as_of_dt.replace(tzinfo=UTC)
    age_minutes = max(0, (now - as_of_dt).total_seconds() / 60.0)
    stale_threshold = prefs.interval_minutes * 1.5
    return Mark(
        ticker=ticker,
        price=price,
        as_of=as_of_dt.date(),
        age_days=(now.date() - as_of_dt.date()).days,
        stale=age_minutes > stale_threshold,
        as_of_datetime=as_of_dt,
    )


def reserved_short_proceeds(portfolio: Portfolio) -> Decimal:
    """Σ(abs(short_quantity) * avg_cost). The short proceeds are credited to
    ``cash_balance`` but cannot back a long order or a withdrawal."""
    total = Decimal("0")
    for pos in portfolio.positions.all():
        if pos.quantity < 0:
            total += pos.quantity.copy_abs() * pos.avg_cost
    return _money(total)


def free_cash(portfolio: Portfolio) -> Decimal:
    return _money(portfolio.cash_balance - reserved_short_proceeds(portfolio))


def value_portfolio(
    portfolio: Portfolio,
    *,
    on: date_cls | None = None,
) -> PortfolioValuation:
    """Mark every position to market and return aggregated exposures."""
    user = portfolio.user
    as_of = on or timezone.now().date()
    positions = list(portfolio.positions.all().order_by("ticker"))
    prefs = get_or_create_preferences(user)

    # First pass: fetch marks. We deliberately keep this serial — bar
    # caching + Redis short-TTL keep it quick for typical book sizes.
    marks: dict[str, Mark | None] = {}
    portfolio_warnings: list[str] = []
    for pos in positions:
        if pos.ticker not in marks:
            try:
                marks[pos.ticker] = get_mark(
                    pos.ticker, as_of, user=user, preferences=prefs,
                )
            except RuntimeError as exc:  # missing FMP key → bubble actionable msg
                portfolio_warnings.append(str(exc))
                marks[pos.ticker] = None

    # Second pass: compute per-position valuation.
    valuations: list[PositionValuation] = []
    long_mv = Decimal("0")
    short_mv = Decimal("0")
    unrealized = Decimal("0")
    realized = Decimal("0")
    for pos in positions:
        mark = marks.get(pos.ticker)
        warnings: list[str] = []
        if mark is None:
            mark_price = pos.avg_cost  # fall back to cost so MV ≠ 0
            mark_as_of: date_cls | None = None
            mark_stale = True
            warnings.append(
                "No daily close available — using average cost as mark."
            )
        else:
            mark_price = mark.price
            mark_as_of = mark.as_of
            mark_stale = mark.stale
            if mark_stale:
                warnings.append(
                    f"Stale mark — last close {mark.age_days} days ago."
                )

        market_value = (pos.quantity * mark_price).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP,
        )
        # Long: (mark - avg_cost) * qty.   Short (qty<0): (avg_cost - mark) * |qty|.
        if pos.quantity >= 0:
            pos_unrealized = (mark_price - pos.avg_cost) * pos.quantity
        else:
            pos_unrealized = (pos.avg_cost - mark_price) * pos.quantity.copy_abs()
        pos_unrealized = pos_unrealized.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        cost_basis = (pos.avg_cost * pos.quantity.copy_abs())
        unrealized_pct: Decimal | None
        if cost_basis != 0:
            unrealized_pct = (pos_unrealized / cost_basis * Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )
        else:
            unrealized_pct = None

        # Corporate-action badge: only warn for splits AFTER the position opened.
        ca_exists = CorporateAction.objects.filter(
            ticker=pos.ticker,
            kind=CorporateAction.SPLIT,
            as_of_date__gt=pos.opened_at.date(),
        ).exists()
        if ca_exists:
            warnings.append(
                "A split occurred after this position opened — quantity may need adjustment."
            )

        valuations.append(
            PositionValuation(
                id=pos.id,
                ticker=pos.ticker,
                quantity=pos.quantity,
                avg_cost=pos.avg_cost,
                is_short=pos.is_short,
                sector=pos.sector,
                opened_at=pos.opened_at,
                opened_via=pos.opened_via,
                source_run_id=pos.source_run_id,
                source_decision_id=pos.source_decision_id,
                note=pos.note,
                realized_pnl=pos.realized_pnl,
                mark_price=mark_price,
                mark_as_of=mark_as_of,
                mark_stale=mark_stale,
                market_value=market_value,
                unrealized_pnl=pos_unrealized,
                unrealized_pnl_pct=unrealized_pct,
                weight_pct=Decimal("0"),  # filled below
                warnings=warnings,
            )
        )
        if pos.quantity >= 0:
            long_mv += market_value
        else:
            short_mv += market_value  # short market_value is negative
        unrealized += pos_unrealized

    # Realized P&L is the ledger sum — fully-closed positions delete the
    # Position row but the LedgerEntry retains the realized_pnl history.
    realized = (
        LedgerEntry.objects.filter(portfolio=portfolio).aggregate(
            total=Sum("realized_pnl"),
        )["total"]
        or Decimal("0")
    )

    reserved = reserved_short_proceeds(portfolio)
    free = _money(portfolio.cash_balance - reserved)
    total_value = _money(portfolio.cash_balance + long_mv + short_mv)

    # Fill weight_pct now that total_value is known.
    if total_value > 0:
        for v in valuations:
            v.weight_pct = (v.market_value / total_value * Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )

    if total_value > 0:
        gross_abs = sum((v.market_value.copy_abs() for v in valuations), Decimal("0"))
        net_abs = sum((v.market_value for v in valuations), Decimal("0"))
        gross_pct = (gross_abs / total_value * Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP,
        )
        net_pct = (net_abs / total_value * Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP,
        )
    else:
        gross_pct = Decimal("0")
        net_pct = Decimal("0")

    # Stamp last_refreshed_at to "now" whenever we successfully marked at
    # least one position (or the book is empty — refreshing an empty book
    # is still a valid no-op refresh).
    if not portfolio_warnings or any(m is not None for m in marks.values()) or not positions:
        prefs.last_refreshed_at = timezone.now()
        prefs.save(update_fields=["last_refreshed_at", "updated_at"])

    return PortfolioValuation(
        portfolio_id=portfolio.id,
        name=portfolio.name,
        kind=portfolio.kind,
        cash_balance=_money(portfolio.cash_balance),
        reserved_short_proceeds=reserved,
        free_cash=free,
        total_value=total_value,
        long_market_value=_money(long_mv),
        short_market_value=_money(short_mv),
        gross_exposure_pct=gross_pct,
        net_exposure_pct=net_pct,
        unrealized_pnl=_money(unrealized),
        realized_pnl=_money(realized),
        positions=valuations,
        preferences=PreferencesView(
            mark_cadence=prefs.mark_cadence,
            interval_minutes=prefs.interval_minutes,
            last_refreshed_at=prefs.last_refreshed_at,
        ),
        warnings=portfolio_warnings,
    )
