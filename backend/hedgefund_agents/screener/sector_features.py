"""Sector-level feature pass for the sector-rotation flavor (P2h).

Inputs are ETFs from the SectorETF registry. Features are intentionally
small: relative momentum vs SPY, drawdown, regime-fit (dot product of the
P2b macro regime vector with the ETF's hand-curated affinities). No LLM
inside this module — it's a deterministic ranker the council fans out on.

Falls back to synthetic deterministic features when the FMP cache has no
bars, so the ranking stays reproducible offline (mirrors the single-name
screener fallback at hedgefund_agents/screener/features.py).
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from apps.data.providers import FmpProvider

# Macro regime axes used to encode the P2b snapshot as a 6-dim vector.
REGIME_AXES = (
    "early_cycle", "mid_cycle", "late_cycle",
    "recession", "rising_rates", "sticky_inflation",
)


@dataclass
class SectorFeatures:
    ticker: str
    sector: str
    theme: str = ""
    relative_momentum_1m: float = 0.0
    relative_momentum_3m: float = 0.0
    relative_momentum_6m: float = 0.0
    drawdown_from_high: float = 0.0
    regime_fit: float = 0.0
    breadth_quality: str = "missing"  # exact | approximate | missing
    last_close: float = 0.0
    available: bool = True
    synthetic: bool = False


DEFAULT_SECTOR_WEIGHTS: dict[str, float] = {
    "relative_momentum_3m": 0.30,
    "relative_momentum_1m": 0.20,
    "relative_momentum_6m": 0.15,
    "drawdown_from_high": 0.15,    # less drawdown → higher score
    "regime_fit": 0.20,
}


def macro_regime_vector(snapshot) -> dict[str, float]:
    """Encode a MacroSnapshot into the 6-dim regime vector.

    Heuristic: derive axis weights from the textual classifications. A miss
    or no-snapshot returns the zero vector, which makes regime_fit a no-op
    (the user still gets a clean momentum-only ranking).
    """
    v = {k: 0.0 for k in REGIME_AXES}
    if snapshot is None:
        return v
    growth = (snapshot.growth_quadrant or "").lower()
    infl = (snapshot.inflation_regime or "").lower()
    yc = (snapshot.yield_curve_state or "").lower()
    pol = (snapshot.policy_stance or "").lower()
    if "expansion" in growth or "early" in growth:
        v["early_cycle"] = 1.0
    if "mid" in growth or "growth" in growth:
        v["mid_cycle"] = 1.0
    if "late" in growth or "slowing" in growth:
        v["late_cycle"] = 1.0
    if "contraction" in growth or "recession" in growth:
        v["recession"] = 1.0
    if "hawkish" in pol or "tightening" in pol or "inverted" in yc:
        v["rising_rates"] = 1.0
    if "high" in infl or "sticky" in infl or "elevated" in infl:
        v["sticky_inflation"] = 1.0
    return v


def _regime_fit(affinities: dict, regime: dict[str, float]) -> float:
    if not affinities or not regime:
        return 0.0
    return sum(float(affinities.get(a, 0.0)) * regime.get(a, 0.0) for a in REGIME_AXES)


def _synthetic_fallback(ticker: str, as_of: date) -> tuple[float, float, float, float, float]:
    h = hashlib.sha1(f"sector|{ticker}|{as_of.isoformat()}".encode()).digest()
    def f(i, lo, hi):
        return lo + (h[i] / 255.0) * (hi - lo)
    return (
        f(0, -0.04, 0.05),   # rm_1m
        f(1, -0.10, 0.15),   # rm_3m
        f(2, -0.15, 0.20),   # rm_6m
        -abs(f(3, 0.0, 0.25)),  # drawdown
        f(4, 50.0, 600.0),   # last_close
    )


def _etf_momentum(provider: FmpProvider, ticker: str, as_of: date) -> tuple[list[float], bool]:
    try:
        start = as_of - timedelta(days=400)
        bars = provider.get_daily_bars(ticker, start=start, end=as_of, as_of=as_of)
        closes = [float(b.close) for b in bars if b.close]
        if len(closes) >= 30:
            return closes, True
    except Exception:
        pass
    return [], False


def compute_sector_features(
    ticker: str,
    sector: str,
    theme: str,
    affinities: dict,
    as_of: date,
    *,
    benchmark_returns: dict[str, float] | None = None,
    regime_vector: dict[str, float] | None = None,
    provider: FmpProvider | None = None,
) -> SectorFeatures:
    provider = provider or FmpProvider()
    feat = SectorFeatures(ticker=ticker, sector=sector, theme=theme)
    closes, real = _etf_momentum(provider, ticker, as_of)
    if real:
        feat.last_close = closes[-1]
        m1 = closes[-1] / closes[-min(21, len(closes))] - 1.0
        m3 = closes[-1] / closes[-min(63, len(closes))] - 1.0
        m6 = closes[-1] / closes[0] - 1.0
        rolling_high = max(closes[-min(252, len(closes)):])
        feat.drawdown_from_high = closes[-1] / rolling_high - 1.0
        feat.synthetic = False
    else:
        m1, m3, m6, dd, last = _synthetic_fallback(ticker, as_of)
        feat.last_close = last
        feat.drawdown_from_high = dd
        feat.synthetic = True

    bench = benchmark_returns or {}
    feat.relative_momentum_1m = m1 - bench.get("1m", 0.0)
    feat.relative_momentum_3m = m3 - bench.get("3m", 0.0)
    feat.relative_momentum_6m = m6 - bench.get("6m", 0.0)
    feat.regime_fit = _regime_fit(affinities, regime_vector or {})
    feat.breadth_quality = "missing"  # exact/approximate gated on holdings feed (P3+).
    feat.available = True
    return feat


def benchmark_returns(provider: FmpProvider, benchmark: str, as_of: date) -> dict[str, float]:
    closes, real = _etf_momentum(provider, benchmark, as_of)
    if not real:
        # Zero benchmark → relative_momentum reduces to absolute momentum.
        return {"1m": 0.0, "3m": 0.0, "6m": 0.0}
    return {
        "1m": closes[-1] / closes[-min(21, len(closes))] - 1.0,
        "3m": closes[-1] / closes[-min(63, len(closes))] - 1.0,
        "6m": closes[-1] / closes[0] - 1.0,
    }


def sector_score(f: SectorFeatures, weights: dict[str, float] | None = None) -> float:
    w = {**DEFAULT_SECTOR_WEIGHTS, **(weights or {})}
    # Drawdown sign: less drawdown (closer to zero) is better, so we invert.
    return (
        w["relative_momentum_3m"] * f.relative_momentum_3m
        + w["relative_momentum_1m"] * f.relative_momentum_1m
        + w["relative_momentum_6m"] * f.relative_momentum_6m
        # drawdown is negative; bigger drawdown lowers the score
        + w["drawdown_from_high"] * f.drawdown_from_high
        + w["regime_fit"] * f.regime_fit
    )


def _rationale(f: SectorFeatures, score: float) -> str:
    bits = []
    if f.relative_momentum_3m > 0.02:
        bits.append(f"3m vs SPY {f.relative_momentum_3m:+.1%}")
    elif f.relative_momentum_3m < -0.02:
        bits.append(f"3m vs SPY {f.relative_momentum_3m:+.1%}")
    if abs(f.regime_fit) > 0.2:
        bits.append(f"regime-fit {f.regime_fit:+.2f}")
    if f.drawdown_from_high < -0.10:
        bits.append(f"drawdown {f.drawdown_from_high:+.1%}")
    if not bits:
        bits.append("composite sector signal")
    return f"Sector candidate ({', '.join(bits)}); score {score:+.2f}"


def run_sector_screener(
    *,
    etfs: list[dict],   # [{ticker, sector, theme, affinities}, ...]
    as_of_date: date,
    top_k: int,
    benchmark: str = "SPY",
    regime_vector: dict[str, float] | None = None,
    weights: dict[str, float] | None = None,
) -> dict:
    provider = FmpProvider()
    bench = benchmark_returns(provider, benchmark, as_of_date)
    feats: list[SectorFeatures] = []
    for e in etfs:
        feats.append(compute_sector_features(
            ticker=e["ticker"],
            sector=e["sector"],
            theme=e.get("theme", ""),
            affinities=e.get("affinities") or e.get("regime_affinities") or {},
            as_of=as_of_date,
            benchmark_returns=bench,
            regime_vector=regime_vector,
            provider=provider,
        ))
    ranked = [(f, sector_score(f, weights)) for f in feats]
    ranked.sort(key=lambda x: x[1], reverse=True)
    candidates = [
        {
            "ticker": f.ticker,
            "sector": f.sector,
            "theme": f.theme,
            "score": round(s, 4),
            "features": asdict(f),
            "rationale": _rationale(f, s),
        }
        for f, s in ranked[:top_k]
    ]
    return {
        "as_of_date": as_of_date.isoformat(),
        "universe_size_evaluated": len(etfs),
        "long_candidates": candidates,
        "short_candidates": [],
        "benchmark": benchmark,
        "benchmark_returns": bench,
    }
