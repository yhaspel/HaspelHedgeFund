"""Per-ticker deterministic feature vector for the Screener.

No LLM. All inputs are point-in-time safe — they pull from FMP/EDGAR/news
caches keyed on `as_of_date`. Missing values default to neutral (0.0).
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import date, timedelta

from apps.data.providers import FmpProvider


@dataclass
class ScreenerFeatures:
    ticker: str
    sector: str
    # Long-attractiveness raw signals (higher = better long)
    momentum_1m: float = 0.0
    momentum_3m: float = 0.0
    momentum_6m: float = 0.0
    # P7: low-volatility factor. Stored as the NEGATIVE annualised realised vol
    # so "higher = better long" holds (a calmer name scores higher). Default 0.0
    # is neutral; only strategies that weight `low_vol` (Account 1) feel it.
    low_vol: float = 0.0
    earnings_yield: float = 0.0          # 1 / PE; higher = cheaper
    quality_roic: float = 0.0
    fcf_margin: float = 0.0
    debt_to_equity: float = 0.0          # lower better; gets sign-flipped in score
    # Short-attractiveness raw signals (higher magnitude = better short)
    drawdown_from_high: float = 0.0      # negative number (down X%)
    news_negative_score: float = 0.0     # 0..1
    # Static metadata
    last_close: float = 0.0
    market_cap: float = 0.0
    available: bool = True
    # True when the feature row was filled from a synthetic sha1-derived
    # fallback (no provider data). Surfaced on the ranking output so the
    # UI / paper-trading layer can distinguish real signals from filler.
    synthetic: bool = False
    # Names of feature buckets that came from a real provider this run.
    # Empty list means "everything is synthetic". A future hardening pass
    # will widen this to include fundamentals/news/borrow flags.
    real_signals: tuple = ()


def _safe(x, default=0.0) -> float:
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except (TypeError, ValueError):
        return default


def _placeholder_features_enabled() -> bool:
    """Synthetic (sha1-derived) feature rows are a *local development* prop
    only. They are never produced in a normal deployment: a ticker with no
    price history must not be scored against real names."""
    try:
        from django.conf import settings
    except Exception:  # pragma: no cover — Django always present in-app
        return False
    return bool(getattr(settings, "OFFLINE_MODE", False))


def _synthetic_fallback(ticker: str, as_of: date) -> ScreenerFeatures:
    """Deterministic features derived from sha1(ticker+as_of) so the screener
    still produces a stable, reproducible ranking when the data provider has
    nothing cached (offline dev only — see ``_placeholder_features_enabled``).

    Rows produced here are always ``synthetic=True`` and are EXCLUDED from the
    ranking by ``screener_agent.run_screener``."""
    h = hashlib.sha1(f"{ticker}|{as_of.isoformat()}".encode()).digest()
    def f(i, lo, hi):
        return lo + (h[i] / 255.0) * (hi - lo)
    feats = ScreenerFeatures(ticker=ticker, sector="")
    feats.momentum_1m = f(0, -0.10, 0.15)
    feats.momentum_3m = f(1, -0.20, 0.30)
    feats.momentum_6m = f(2, -0.30, 0.50)
    feats.drawdown_from_high = -abs(f(3, 0.0, 0.40))
    feats.earnings_yield = f(4, 0.02, 0.12)
    feats.quality_roic = f(5, -0.05, 0.30)
    feats.fcf_margin = f(6, -0.05, 0.25)
    feats.debt_to_equity = f(7, 0.0, 3.0)
    feats.news_negative_score = f(8, 0.0, 1.0)
    feats.low_vol = -f(11, 0.10, 0.45)   # negative annualised vol (higher = calmer)
    feats.last_close = f(9, 10.0, 500.0)
    feats.market_cap = f(10, 1e9, 3e12)
    feats.available = True
    feats.synthetic = True
    feats.real_signals = ()
    return feats


def compute_features(
    ticker: str, sector: str, as_of: date, *, provider: FmpProvider
) -> ScreenerFeatures:
    """Try the FMP cache. When the provider has no usable price history the
    row is returned ``available=False`` so the ranker can drop it — a ticker
    with no data must never compete with real names (it used to be filled
    from sha1(ticker|as_of), which handed no-data tickers random
    fundamentals/news scores the real path never populates).

    P2n: ``provider`` is now required — callers must obtain one from
    ``apps.data.providers.factory.get_fmp_provider(user=...)`` so the BYOK
    gate covers every read path.
    """
    feats = ScreenerFeatures(ticker=ticker, sector=sector)
    closes: list[float] = []
    try:
        start = as_of - timedelta(days=400)
        bars = provider.get_daily_bars(ticker, start=start, end=as_of, as_of=as_of)
        # Total-return series: adjusted_close matches the Markov classifier and
        # the backtest engine (dividends credited). Fall back to close only for
        # providers/fixtures that do not populate it.
        closes = [float(b.adjusted_close or b.close) for b in bars if (b.adjusted_close or b.close)]
    except Exception:
        closes = []
    if len(closes) >= 30:
        feats.last_close = closes[-1]
        feats.momentum_1m = (closes[-1] / closes[-min(21, len(closes))] - 1.0)
        feats.momentum_3m = (closes[-1] / closes[-min(63, len(closes))] - 1.0)
        # ~6 calendar months of sessions (was closes[0] over a 400-CALENDAR-day
        # window, i.e. a ~13-month return labelled "6m").
        feats.momentum_6m = (closes[-1] / closes[-min(127, len(closes))] - 1.0)
        rolling_high = max(closes[-min(252, len(closes)):])
        feats.drawdown_from_high = closes[-1] / rolling_high - 1.0
        # P7 low-vol factor: annualised realised vol over the trailing window
        # (≈1 quarter), stored negated so a calmer name scores higher.
        window = closes[-min(63, len(closes)):]
        rets = [window[i] / window[i - 1] - 1.0 for i in range(1, len(window)) if window[i - 1] > 0]
        if len(rets) >= 2:
            mean = sum(rets) / len(rets)
            var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
            feats.low_vol = -math.sqrt(max(0.0, var)) * math.sqrt(252)
        feats.available = True
        feats.synthetic = False
        feats.real_signals = ("price_momentum", "drawdown", "low_vol")
        feats.sector = sector
        return feats
    # No usable price history. Offline dev keeps the deterministic placeholder
    # (still flagged synthetic, still excluded from the ranking); production
    # marks the row unavailable so it is dropped rather than scored.
    if _placeholder_features_enabled():
        syn = _synthetic_fallback(ticker, as_of)
        syn.sector = sector
        return syn
    feats.available = False
    feats.synthetic = True
    feats.real_signals = ()
    return feats


def long_score(f: ScreenerFeatures, weights: dict[str, float]) -> float:
    """Higher = better long candidate.

    Unavailable *and* synthetic rows score -1e9 so ``run_screener``'s
    ``s > -1e8`` filter drops them: placeholder values must never rank.
    """
    if not f.available or f.synthetic:
        return -1e9
    return (
        weights.get("momentum_3m", 1.0) * f.momentum_3m
        + weights.get("momentum_6m", 0.5) * f.momentum_6m
        + weights.get("earnings_yield", 1.0) * f.earnings_yield
        + weights.get("quality_roic", 1.0) * f.quality_roic
        + weights.get("fcf_margin", 1.0) * f.fcf_margin
        - weights.get("debt_to_equity", 0.25) * f.debt_to_equity
        + weights.get("low_vol", 0.0) * f.low_vol
    )


def short_score(f: ScreenerFeatures, weights: dict[str, float]) -> float:
    """Higher = better short candidate (most negative momentum/quality)."""
    if not f.available or f.synthetic:
        return -1e9
    return (
        weights.get("short_drawdown", 1.0) * (-f.drawdown_from_high)
        + weights.get("short_momentum_3m", 1.0) * (-f.momentum_3m)
        + weights.get("short_news_neg", 1.0) * f.news_negative_score
        + weights.get("short_debt", 0.5) * f.debt_to_equity
        - weights.get("short_quality_roic", 0.5) * f.quality_roic
    )


DEFAULT_WEIGHTS: dict[str, float] = {
    # Longs
    "momentum_3m": 1.0,
    "momentum_6m": 0.5,
    "earnings_yield": 1.0,
    "quality_roic": 1.0,
    "fcf_margin": 1.0,
    "debt_to_equity": 0.25,
    "low_vol": 0.0,          # P7: off by default; Account 1 opts in (§3).
    # Shorts
    "short_drawdown": 1.0,
    "short_momentum_3m": 1.0,
    "short_news_neg": 1.0,
    "short_debt": 0.5,
    "short_quality_roic": 0.5,
}
