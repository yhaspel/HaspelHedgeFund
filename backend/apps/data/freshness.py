"""Live-cycle market-data freshness + adjusted-close integrity guard (P10 §A1).

Two pre-cycle hazards the deterministic fund pods are exposed to:

1. **Staleness.** The on-demand bar cache (`FmpProvider._ensure_bars_cached`)
   skips fetching the most recent sessions when ~60% of the *long* lookback
   window is already populated, so a weekly cycle can size on bars several
   sessions old.
2. **Adjusted-close corruption.** FMP's `/historical-price-eod/dividend-adjusted`
   endpoint returns spurious adjustment factors on the most recent bars (a
   ~1-3% phantom move with no matching dividend). The deterministic sizers read
   ``adjusted_close`` for momentum/vol, so a corrupt tail bar injects a fake
   return into the most recent signal.

The provable invariant we lean on: **after a ticker's last cash-dividend
ex-date, total return == price return, so ``adjusted_close`` must equal
``close``.** `normalize_adjusted_tail` resets exactly those tail bars (leaving
the genuine back-adjusted history untouched); `refresh_universe_bars`
force-fetches the recent tail (bypassing the cache heuristic) and normalizes it;
`assert_universe_fresh` then refuses to let the cycle trade on data that is still
stale or whose latest adjusted close disagrees with the raw close with no
dividend to explain it.
"""
from __future__ import annotations

import datetime as dt
import logging

from django.db.models import F

from apps.data.models import CorporateAction, DailyBar

log = logging.getLogger(__name__)

SOURCE = "fmp"
MAX_STALENESS_DAYS = 5  # calendar days; tolerates a long holiday weekend
FACTOR_TOL = 0.005  # |adj/close - 1| beyond this, with no same-day action, = corrupt
# Corporate actions that make adjusted_close != close for bars BEFORE their
# ex-date (cash + stock dividends and splits). A bar on/after the LAST such
# action has nothing left to adjust, so its adjusted_close must equal close.
_ADJUSTMENT_KINDS = (
    CorporateAction.CASH_DIVIDEND,
    CorporateAction.STOCK_DIVIDEND,
    CorporateAction.SPLIT,
)


class StaleMarketDataError(RuntimeError):
    """Raised when a deterministic cycle's universe data is too stale or its
    latest adjusted close is corrupt — the cycle must skip rather than trade."""


def last_adjustment_ex_date(ticker: str) -> dt.date | None:
    """Ex-date of the ticker's most recent price-adjusting corporate action
    (dividend or split). None if it has never had one."""
    return (
        CorporateAction.objects.filter(ticker=ticker, kind__in=_ADJUSTMENT_KINDS)
        .order_by("-as_of_date")
        .values_list("as_of_date", flat=True)
        .first()
    )


def normalize_adjusted_tail(ticker: str) -> int:
    """Reset ``adjusted_close = close`` for every bar dated on/after the ticker's
    last price-adjusting corporate action (or all bars if it never had one).

    Provably correct: no dividend/split on-or-after date D ⟹ total return ==
    price return ⟹ adjusted_close == close for all bars ≥ D. Bars strictly
    before D keep their back-adjustment (a split with no later dividend would
    otherwise have its whole split-adjusted history wrongly flattened). Returns
    the number of rows corrected.
    """
    led = last_adjustment_ex_date(ticker)
    # Only rows where adjusted_close already disagrees with close need touching —
    # bounds the scan to actual corrections (a no-dividend ticker re-scanned each
    # cycle does no work).
    qs = DailyBar.objects.filter(ticker=ticker, source=SOURCE).exclude(
        adjusted_close=F("close")
    )
    if led is not None:
        qs = qs.filter(date__gte=led)
    fixed = []
    for b in qs:
        b.adjusted_close = b.close
        fixed.append(b)
    if fixed:
        DailyBar.objects.bulk_update(fixed, ["adjusted_close"], batch_size=2000)
    return len(fixed)


def refresh_universe_bars(tickers, as_of: dt.date, provider, *, lookback_days: int = 20) -> None:
    """Force-fetch the recent tail for each ticker (bypassing the cache
    completeness heuristic) and normalize its adjusted tail, so the live cycle
    sizes on current, integrity-checked closes. Best-effort per ticker — the
    subsequent `assert_universe_fresh` is the hard gate."""
    start = as_of - dt.timedelta(days=lookback_days)
    for t in tickers:
        try:
            provider.upsert_recent_bars(t, start, as_of)
        except Exception:  # noqa: BLE001 — degrade to whatever is cached; assert gates
            log.exception("refresh_universe_bars: upsert failed ticker=%s", t)
        try:
            normalize_adjusted_tail(t)
        except Exception:  # noqa: BLE001
            log.exception("refresh_universe_bars: normalize failed ticker=%s", t)


def assert_universe_fresh(
    tickers,
    as_of: dt.date,
    *,
    max_staleness_days: int = MAX_STALENESS_DAYS,
    factor_tol: float = FACTOR_TOL,
) -> None:
    """Raise `StaleMarketDataError` if any ticker's latest bar (≤ as_of) is older
    than `max_staleness_days`, has a non-positive close, or its adjusted close
    deviates from the raw close by more than `factor_tol` with no corporate action
    on that exact date to explain it."""
    problems: list[str] = []
    for t in tickers:
        b = (
            DailyBar.objects.filter(ticker=t, source=SOURCE, date__lte=as_of)
            .order_by("-date")
            .first()
        )
        if b is None:
            problems.append(f"{t}: no bars on/before {as_of}")
            continue
        staleness = (as_of - b.date).days
        if staleness > max_staleness_days:
            problems.append(
                f"{t}: latest bar {b.date} is {staleness}d stale (limit {max_staleness_days})"
            )
        close = float(b.close)
        if close <= 0:
            problems.append(f"{t}: latest bar {b.date} has non-positive close ({close}) — invalid")
        else:
            factor = float(b.adjusted_close) / close
            if abs(factor - 1.0) > factor_tol:
                has_action = CorporateAction.objects.filter(
                    ticker=t, kind__in=_ADJUSTMENT_KINDS, as_of_date=b.date
                ).exists()
                if not has_action:
                    problems.append(
                        f"{t}: latest bar {b.date} adj/close={factor:.4f} "
                        f"(|Δ|>{factor_tol}) with no corporate action — corrupt"
                    )
    if problems:
        raise StaleMarketDataError("; ".join(problems))
