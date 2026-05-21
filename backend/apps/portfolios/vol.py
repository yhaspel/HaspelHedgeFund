"""Rolling-window daily-return volatility estimator (P2j risk parity).

Trailing close-to-close returns over `window_days`, ending strictly on
`as_of_date` (no look-ahead). Synthetic deterministic fallback for offline
reproducibility — mirrors beta.py / sector_features.py.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import timedelta
from decimal import Decimal

from .models import VolEstimate

MIN_OBS = 20


@dataclass
class VolResult:
    ticker: str
    as_of: date_cls
    window_days: int
    daily_vol: float
    annualised_vol: float
    n_observations: int
    synthetic: bool


def _daily_returns(bars: list) -> list[float]:
    out: list[float] = []
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
            out.append((px / prev) - 1.0)
        prev = px
    return out


def _stdev(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    v = sum((x - m) ** 2 for x in xs) / (n - 1)
    return math.sqrt(max(0.0, v))


def _synthetic(ticker: str, as_of: date_cls) -> float:
    h = hashlib.sha1(f"vol|{ticker}|{as_of.isoformat()}".encode()).digest()
    # Map to daily-vol in [0.004, 0.035] — covers low-vol bonds to high-vol thematics.
    return 0.004 + (h[0] / 255.0) * 0.031


def compute_vol(
    ticker: str,
    as_of: date_cls,
    *,
    window_days: int,
    data_provider,
    use_cache: bool = True,
) -> VolResult:
    if use_cache:
        cached = VolEstimate.objects.filter(
            ticker=ticker, as_of_date=as_of, window_days=window_days,
        ).first()
        if cached:
            return VolResult(
                ticker=ticker, as_of=as_of, window_days=window_days,
                daily_vol=float(cached.daily_vol),
                annualised_vol=float(cached.annualised_vol),
                n_observations=int(cached.n_observations),
                synthetic=bool(cached.synthetic),
            )

    start = as_of - timedelta(days=int(window_days * 1.6) + 14)
    try:
        bars = data_provider.get_daily_bars(ticker, start=start, end=as_of, as_of=as_of) or []
    except Exception:
        bars = []
    rets = _daily_returns(bars)
    rets = rets[-window_days:]
    n = len(rets)
    synthetic = False
    if n < MIN_OBS:
        daily = _synthetic(ticker, as_of)
        synthetic = True
        n = 0
    else:
        daily = _stdev(rets)
        if daily <= 0:
            daily = _synthetic(ticker, as_of)
            synthetic = True
    ann = daily * math.sqrt(252)

    VolEstimate.objects.update_or_create(
        ticker=ticker, as_of_date=as_of, window_days=window_days,
        defaults={
            "daily_vol": Decimal(str(round(daily, 6))),
            "annualised_vol": Decimal(str(round(ann, 6))),
            "n_observations": n,
            "synthetic": synthetic,
        },
    )
    return VolResult(
        ticker=ticker, as_of=as_of, window_days=window_days,
        daily_vol=daily, annualised_vol=ann,
        n_observations=n, synthetic=synthetic,
    )


def compute_vols_for(
    tickers: list[str], as_of: date_cls, *, window_days: int, data_provider,
) -> dict[str, VolResult]:
    out: dict[str, VolResult] = {}
    for t in set(tickers):
        out[t] = compute_vol(t, as_of, window_days=window_days, data_provider=data_provider)
    return out
