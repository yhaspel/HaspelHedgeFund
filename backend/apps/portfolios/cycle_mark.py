"""P3 addendum: per-cycle mark-to-market snapshot for ``PortfolioTarget``.

Treats a strategy cycle's ``target_weights`` (signed fractions of NAV) as
a *hypothetical-hold* book and computes the forward return since the
cycle's ``as_of_date`` — without requiring broker fills.

Per-ticker numbers:
  - ``weight_pct``       — signed weight from ``target_weights``, in pp.
  - ``as_of_price``      — last daily close on/before ``as_of_date``.
  - ``mark_price``       — latest daily close at snapshot time.
  - ``return_pct``       — ``(mark - as_of) / as_of`` in pp (signed).
  - ``contribution_pp``  — ``weight_pct * return_pct / 100`` in pp.

Book aggregates:
  - ``since_as_of_pct``  — Σ(contribution_pp). Positive = book moved with
                            the cycle's bets since dispatch.
  - ``marked_gross_pct`` — Σ(|w| × (1 + r)) in pp.
  - ``marked_net_pct``   — Σ(w × (1 + r)) in pp.

The snapshot is persisted on ``PortfolioTarget.marked_snapshot``. For
done cycles older than the latest trading day it's effectively stable
(only the "mark" leg updates as new closes arrive); for fresh cycles a
re-stamp picks up the same-day mark.
"""
from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.utils import timezone

from apps.data.providers.factory import get_fmp_provider

from .models import PortfolioTarget

# How long to wait before re-stamping a snapshot when re-rendering the
# cycle detail. Marks come from daily closes, so 10 minutes is plenty —
# matches the Manual Book daily cadence TTL.
SNAPSHOT_FRESH_FOR_SECONDS = 600

# Generous lookback so weekend / holiday cycles still find a close.
LOOKBACK_DAYS = 14


def _quantize_pp(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _last_close_on_or_before(
    provider: Any, ticker: str, on: date_cls,
) -> tuple[Decimal | None, date_cls | None]:
    """Latest daily close on or before ``on``. Returns ``(price, date)``."""
    start = on - timedelta(days=LOOKBACK_DAYS)
    try:
        bars = provider.get_daily_bars(ticker, start, on, as_of=on)
    except Exception:  # pragma: no cover — provider transient errors
        return None, None
    if not bars:
        return None, None
    last = bars[-1]
    return Decimal(str(last.close)), last.date


def compute_cycle_snapshot(
    target: PortfolioTarget,
    *,
    on: date_cls | None = None,
) -> dict[str, Any]:
    """Compute a marked snapshot for ``target`` without persisting.

    Returns the JSON-serializable dict that gets stored on
    ``PortfolioTarget.marked_snapshot``.
    """
    user = target.strategy.user
    snapshot_at = timezone.now()
    mark_as_of = on or snapshot_at.date()

    weights = target.target_weights or {}
    tickers = sorted(weights.keys())

    per_ticker: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    since_as_of_pp = Decimal("0")
    marked_gross_pp = Decimal("0")
    marked_net_pp = Decimal("0")

    if not tickers:
        return {
            "snapshot_at": snapshot_at.isoformat(),
            "mark_as_of": mark_as_of.isoformat(),
            "since_as_of_pct": "0.00",
            "marked_gross_pct": "0.00",
            "marked_net_pct": "0.00",
            "per_ticker": {},
            "warnings": ["Empty target book — nothing to mark."],
        }

    try:
        provider = get_fmp_provider(user=user)
    except RuntimeError as exc:
        warnings.append(str(exc))
        return {
            "snapshot_at": snapshot_at.isoformat(),
            "mark_as_of": mark_as_of.isoformat(),
            "since_as_of_pct": None,
            "marked_gross_pct": None,
            "marked_net_pct": None,
            "per_ticker": {},
            "warnings": warnings,
        }

    for ticker in tickers:
        weight_frac = Decimal(str(weights.get(ticker, 0) or 0))
        if weight_frac == 0:
            continue
        weight_pp = weight_frac * Decimal("100")

        as_of_price, as_of_actual_date = _last_close_on_or_before(
            provider, ticker, target.as_of_date,
        )
        mark_price, mark_actual_date = _last_close_on_or_before(
            provider, ticker, mark_as_of,
        )

        per_warnings: list[str] = []
        if as_of_price is None or as_of_price == 0:
            per_warnings.append("no close on or before cycle as_of_date")
        if mark_price is None or mark_price == 0:
            per_warnings.append("no recent close available")

        return_pp: Decimal | None = None
        contribution_pp = Decimal("0")
        marked_weight_factor = Decimal("1")
        if as_of_price and mark_price and as_of_price > 0:
            return_frac = (mark_price - as_of_price) / as_of_price
            return_pp = return_frac * Decimal("100")
            contribution_pp = weight_pp * return_frac
            marked_weight_factor = Decimal("1") + return_frac

        since_as_of_pp += contribution_pp
        marked_gross_pp += weight_pp.copy_abs() * marked_weight_factor
        marked_net_pp += weight_pp * marked_weight_factor

        per_ticker[ticker] = {
            "weight_pct": str(_quantize_pp(weight_pp)),
            "as_of_price": (
                str(_quantize_money(as_of_price)) if as_of_price is not None else None
            ),
            "as_of_price_date": (
                as_of_actual_date.isoformat() if as_of_actual_date else None
            ),
            "mark_price": (
                str(_quantize_money(mark_price)) if mark_price is not None else None
            ),
            "mark_price_date": (
                mark_actual_date.isoformat() if mark_actual_date else None
            ),
            "return_pct": (
                str(_quantize_pp(return_pp)) if return_pp is not None else None
            ),
            "contribution_pp": str(_quantize_pp(contribution_pp)),
            "warnings": per_warnings,
        }

    return {
        "snapshot_at": snapshot_at.isoformat(),
        "mark_as_of": mark_as_of.isoformat(),
        "since_as_of_pct": str(_quantize_pp(since_as_of_pp)),
        "marked_gross_pct": str(_quantize_pp(marked_gross_pp)),
        "marked_net_pct": str(_quantize_pp(marked_net_pp)),
        "per_ticker": per_ticker,
        "warnings": warnings,
    }


def _snapshot_is_fresh(snapshot: dict[str, Any]) -> bool:
    """Same-process cache: a snapshot is fresh for SNAPSHOT_FRESH_FOR_SECONDS
    after it was last stamped. Stale snapshots get recomputed on the next
    cycle-detail read.
    """
    ts = snapshot.get("snapshot_at")
    if not ts:
        return False
    try:
        stamped = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return False
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=timezone.now().tzinfo)
    age = (timezone.now() - stamped).total_seconds()
    return age >= 0 and age < SNAPSHOT_FRESH_FOR_SECONDS


def ensure_cycle_snapshot(
    target: PortfolioTarget,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Return the marked snapshot for ``target``, computing/persisting it
    if missing or stale. Callers should pass ``force=True`` to bypass the
    short-lived freshness check (e.g. a manual "Refresh marks" action).
    """
    existing = target.marked_snapshot or {}
    if existing and not force and _snapshot_is_fresh(existing):
        return existing
    snapshot = compute_cycle_snapshot(target)
    target.marked_snapshot = snapshot
    target.save(update_fields=["marked_snapshot"])
    return snapshot
