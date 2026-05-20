"""Rolling-window beta estimator vs a benchmark (default SPY).

Deterministic OLS on daily log returns over a trailing window strictly
ending on `as_of_date` (no look-ahead). Results are cached in `BetaEstimate`
keyed on (ticker, benchmark, as_of_date, window_days).

Quality gate: if r_squared < 0.05 or n_observations < 60, the estimate is
flagged `reliable=False`. Callers may treat unreliable names as β=1.0
(conservative — fully market-exposed) or drop them per strategy config.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import timedelta
from decimal import Decimal

from .models import BetaEstimate

UNRELIABLE_R2 = 0.05
MIN_OBS = 60


@dataclass
class BetaResult:
    ticker: str
    benchmark: str
    as_of: date_cls
    window_days: int
    beta: float
    r_squared: float
    n_observations: int
    reliable: bool


def _daily_returns(bars: list) -> dict[date_cls, float]:
    """Closing-price daily returns keyed by date. Uses adjusted_close when present."""
    out: dict[date_cls, float] = {}
    prev: float | None = None
    for b in bars:
        try:
            px = float(getattr(b, "adjusted_close", None) or b.close)
        except Exception:
            continue
        if px <= 0:
            prev = None
            continue
        if prev is not None and prev > 0:
            out[b.date] = (px / prev) - 1.0
        prev = px
    return out


def _ols_beta(r_tkr: list[float], r_bm: list[float]) -> tuple[float, float]:
    n = len(r_tkr)
    if n < 2:
        return 0.0, 0.0
    mean_t = sum(r_tkr) / n
    mean_b = sum(r_bm) / n
    cov = sum((r_tkr[i] - mean_t) * (r_bm[i] - mean_b) for i in range(n)) / n
    var_b = sum((x - mean_b) ** 2 for x in r_bm) / n
    var_t = sum((x - mean_t) ** 2 for x in r_tkr) / n
    if var_b <= 0 or var_t <= 0:
        return 0.0, 0.0
    beta = cov / var_b
    # r^2 = (cov^2) / (var_t * var_b)
    r2 = (cov * cov) / (var_t * var_b)
    if math.isnan(beta) or math.isnan(r2):
        return 0.0, 0.0
    return beta, max(0.0, min(1.0, r2))


def compute_beta(
    ticker: str,
    benchmark: str,
    as_of: date_cls,
    *,
    window_days: int,
    data_provider,
    use_cache: bool = True,
) -> BetaResult:
    if use_cache:
        cached = BetaEstimate.objects.filter(
            ticker=ticker, benchmark=benchmark, as_of_date=as_of, window_days=window_days
        ).first()
        if cached:
            return BetaResult(
                ticker=ticker, benchmark=benchmark, as_of=as_of,
                window_days=window_days,
                beta=float(cached.beta), r_squared=float(cached.r_squared),
                n_observations=int(cached.n_observations), reliable=bool(cached.reliable),
            )

    # Pad the window: 252 trading days ≈ 365 calendar days; add headroom.
    start = as_of - timedelta(days=int(window_days * 1.6) + 14)
    try:
        tkr_bars = data_provider.get_daily_bars(ticker, start=start, end=as_of, as_of=as_of) or []
        bm_bars = data_provider.get_daily_bars(benchmark, start=start, end=as_of, as_of=as_of) or []
    except Exception:
        tkr_bars, bm_bars = [], []

    tkr_ret = _daily_returns(tkr_bars)
    bm_ret = _daily_returns(bm_bars)
    common = sorted(set(tkr_ret) & set(bm_ret))
    # Use only the trailing `window_days` observations.
    common = common[-window_days:]
    r_t = [tkr_ret[d] for d in common]
    r_b = [bm_ret[d] for d in common]
    n = len(r_t)

    if n < 2:
        beta_val, r2 = 0.0, 0.0
    else:
        beta_val, r2 = _ols_beta(r_t, r_b)

    reliable = (n >= MIN_OBS) and (r2 >= UNRELIABLE_R2)

    BetaEstimate.objects.update_or_create(
        ticker=ticker, benchmark=benchmark, as_of_date=as_of, window_days=window_days,
        defaults={
            "beta": Decimal(str(round(beta_val, 3))),
            "r_squared": Decimal(str(round(r2, 3))),
            "n_observations": n,
            "reliable": reliable,
        },
    )
    return BetaResult(
        ticker=ticker, benchmark=benchmark, as_of=as_of, window_days=window_days,
        beta=beta_val, r_squared=r2, n_observations=n, reliable=reliable,
    )


def compute_betas_for(
    tickers: list[str], benchmark: str, as_of: date_cls,
    *, window_days: int, data_provider,
) -> dict[str, BetaResult]:
    out: dict[str, BetaResult] = {}
    for t in set(tickers):
        out[t] = compute_beta(
            t, benchmark, as_of, window_days=window_days, data_provider=data_provider
        )
    return out
