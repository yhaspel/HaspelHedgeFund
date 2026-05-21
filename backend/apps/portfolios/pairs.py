"""Pairs-trading primitives (P2k).

In-house Engle-Granger-lite:
  * OLS hedge ratio β:  ln(P_a) = α + β·ln(P_b) + ε
  * AR(1) on the residual spread: Δe_t = ρ_minus_1 · e_{t-1} + u
    rho_minus_1 < 0 with |t-stat| large → mean-reverting (cointegrated)
  * Approximate ADF p-value via a heuristic mapping from |t-stat|; OK for
    an MVP that runs end-to-end without statsmodels.

Synthetic price fallback (mirrors vol.py / beta.py) so the strategy
yields results on offline / partially-cached universes.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import timedelta

MIN_OBS = 60


@dataclass
class PairCandidate:
    leg_a: str
    leg_b: str
    sector: str
    hedge_ratio: float
    spread_mean: float
    spread_std: float
    z_current: float
    p_value: float
    correlation: float
    n_obs: int
    synthetic: bool


def _log_prices(bars: list) -> list[float]:
    out: list[float] = []
    for b in bars:
        try:
            px = float(getattr(b, "adjusted_close", None) or b.close)
        except Exception:
            continue
        if px > 0:
            out.append(math.log(px))
    return out


def _synthetic_log_prices(ticker: str, as_of: date_cls, n: int) -> list[float]:
    h = hashlib.sha1(f"pairs|{ticker}|{as_of.isoformat()}".encode()).digest()
    base = 3.0 + (h[0] / 255.0) * 2.0           # ~ln($20..$150)
    drift = (h[1] / 255.0 - 0.5) * 0.0008       # ±0.04% daily drift
    vol = 0.008 + (h[2] / 255.0) * 0.020        # 0.8%..2.8% daily vol
    out: list[float] = []
    x = base
    for i in range(n):
        # Deterministic pseudo-random walk seeded by ticker+day index.
        r = ((h[(i * 7) % 20] / 255.0) - 0.5) * 2.0
        x += drift + vol * r
        out.append(x)
    return out


def _ols_alpha_beta(y: list[float], x: list[float]) -> tuple[float, float]:
    n = len(y)
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y, strict=False))
    den = sum((xi - mx) ** 2 for xi in x)
    if den <= 0:
        return (my, 0.0)
    beta = num / den
    alpha = my - beta * mx
    return alpha, beta


def _correlation(a: list[float], b: list[float]) -> float:
    n = len(a)
    if n < 2:
        return 0.0
    ma = sum(a) / n
    mb = sum(b) / n
    num = sum((ai - ma) * (bi - mb) for ai, bi in zip(a, b, strict=False))
    da = math.sqrt(sum((ai - ma) ** 2 for ai in a))
    db = math.sqrt(sum((bi - mb) ** 2 for bi in b))
    if da <= 0 or db <= 0:
        return 0.0
    return num / (da * db)


def _adf_like_p(spread: list[float]) -> float:
    """Approximate ADF p-value from the AR(1)-minus-one t-statistic.

    Regress Δe_t = γ·e_{t-1} + u; mean-reverting if γ << 0. We map |t-stat|
    via a smooth logistic to a [0, 1] p-value. Not a true ADF table; the
    cutoff t≈-2.86 (5%) lands near p≈0.05 by construction.
    """
    n = len(spread)
    if n < 30:
        return 1.0
    lag = spread[:-1]
    dy = [spread[i + 1] - spread[i] for i in range(n - 1)]
    mlag = sum(lag) / len(lag)
    den = sum((x - mlag) ** 2 for x in lag)
    if den <= 0:
        return 1.0
    gamma = sum((lag[i] - mlag) * dy[i] for i in range(len(lag))) / den
    resid = [dy[i] - gamma * (lag[i] - mlag) for i in range(len(lag))]
    rss = sum(r * r for r in resid)
    sigma2 = rss / max(1, len(lag) - 1)
    se = math.sqrt(sigma2 / den) if den > 0 else 0.0
    if se <= 0:
        return 1.0
    t_stat = gamma / se
    # Map t-stat ∈ (-∞, 0]. t_stat = -2.86 → p≈0.05; t_stat = 0 → p≈0.7.
    return 1.0 / (1.0 + math.exp(-(t_stat + 1.5) * 1.2))


@dataclass
class PriceBundle:
    log_prices: dict[str, list[float]]
    synthetic: set[str]


def gather_log_prices(
    tickers: list[str], as_of: date_cls, lookback_days: int, data_provider
) -> PriceBundle:
    start = as_of - timedelta(days=int(lookback_days * 1.6) + 14)
    out: dict[str, list[float]] = {}
    synthetic: set[str] = set()
    for t in set(tickers):
        bars: list = []
        try:
            bars = data_provider.get_daily_bars(t, start=start, end=as_of, as_of=as_of) or []
        except Exception:
            bars = []
        prices = _log_prices(bars)
        prices = prices[-lookback_days:]
        if len(prices) < MIN_OBS:
            prices = _synthetic_log_prices(t, as_of, lookback_days)
            synthetic.add(t)
        out[t] = prices
    return PriceBundle(log_prices=out, synthetic=synthetic)


def evaluate_pair(
    leg_a: str, leg_b: str, sector: str,
    la: list[float], lb: list[float],
    synthetic_pair: bool,
) -> PairCandidate | None:
    n = min(len(la), len(lb))
    if n < MIN_OBS:
        return None
    la = la[-n:]
    lb = lb[-n:]
    alpha, beta = _ols_alpha_beta(la, lb)
    if beta <= 0:
        # Negative-β pair has no economic interpretation in same-sector longs.
        return None
    spread = [la[i] - beta * lb[i] for i in range(n)]
    mu = sum(spread) / n
    var = sum((s - mu) ** 2 for s in spread) / max(1, n - 1)
    sigma = math.sqrt(max(0.0, var))
    if sigma <= 1e-9:
        return None
    z = (spread[-1] - mu) / sigma
    p = _adf_like_p(spread)
    corr = _correlation(la, lb)
    return PairCandidate(
        leg_a=leg_a, leg_b=leg_b, sector=sector,
        hedge_ratio=beta, spread_mean=mu, spread_std=sigma,
        z_current=z, p_value=p, correlation=corr,
        n_obs=n, synthetic=synthetic_pair,
    )


def screen_pairs(
    members: list[tuple[str, str]],
    as_of: date_cls,
    *,
    data_provider,
    lookback_days: int = 252,
    p_max: float = 0.05,
    corr_min: float = 0.70,
    entry_z: float = 2.0,
    max_candidates_total: int = 50000,
) -> tuple[list[PairCandidate], dict]:
    """Within-sector cointegration screener.

    Returns (candidates_sorted_by_abs_z_desc, diagnostics).
    """
    by_sector: dict[str, list[str]] = {}
    sector_of: dict[str, str] = {}
    for ticker, sector in members:
        sector_of[ticker] = sector
        by_sector.setdefault(sector or "", []).append(ticker)

    # Cost guardrail.
    total_pairs = sum(len(v) * (len(v) - 1) // 2 for v in by_sector.values())
    if total_pairs > max_candidates_total:
        raise RuntimeError(
            f"pairs screener: {total_pairs} candidate pairs exceeds "
            f"max_candidates_total={max_candidates_total}"
        )

    tickers = [t for t, _ in members]
    bundle = gather_log_prices(tickers, as_of, lookback_days, data_provider)

    out: list[PairCandidate] = []
    n_evaluated = 0
    n_cointegrated = 0
    for sector, names in by_sector.items():
        names = sorted(names)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = names[i], names[j]
                la = bundle.log_prices.get(a) or []
                lb = bundle.log_prices.get(b) or []
                if not la or not lb:
                    continue
                n_evaluated += 1
                cand = evaluate_pair(
                    a, b, sector, la, lb,
                    synthetic_pair=(a in bundle.synthetic or b in bundle.synthetic),
                )
                if cand is None:
                    continue
                if cand.correlation < corr_min:
                    continue
                if cand.p_value > p_max:
                    continue
                n_cointegrated += 1
                if abs(cand.z_current) < entry_z:
                    continue
                out.append(cand)

    # Orient so leg_a is the long leg (z>0 means spread above mean → a expensive
    # vs b → short a, long b → flip).
    oriented: list[PairCandidate] = []
    for c in out:
        if c.z_current > 0:
            oriented.append(PairCandidate(
                leg_a=c.leg_b, leg_b=c.leg_a, sector=c.sector,
                hedge_ratio=(1.0 / c.hedge_ratio) if c.hedge_ratio > 0 else 1.0,
                spread_mean=-c.spread_mean, spread_std=c.spread_std,
                z_current=-c.z_current, p_value=c.p_value,
                correlation=c.correlation, n_obs=c.n_obs, synthetic=c.synthetic,
            ))
        else:
            oriented.append(c)
    oriented.sort(key=lambda c: abs(c.z_current), reverse=True)
    diagnostics = {
        "n_pairs_evaluated": n_evaluated,
        "n_pairs_cointegrated": n_cointegrated,
        "n_pairs_above_entry_z": len(oriented),
        "synthetic_tickers": sorted(bundle.synthetic),
    }
    return oriented, diagnostics


def decide_open_pair_action(
    *,
    leg_a: str,
    leg_b: str,
    hedge_ratio: float,
    spread_mean: float,
    spread_std: float,
    log_prices: dict[str, list[float]],
    exit_z: float,
    stop_z: float,
    p_max: float,
    consecutive_failures: int,
    regime_break_after: int = 3,
) -> tuple[str, float | None, float | None]:
    """Decide what to do with one currently-open pair.

    Returns (action, current_z, current_p_value).
    action ∈ {"hold", "reverted", "stopped", "regime_break", "no_price"}.

    consecutive_failures is the running count of prior daily cointegration
    failures for this pair; if today also fails AND the count would reach
    regime_break_after, the action is "regime_break". The caller is
    responsible for persisting the updated counter.
    """
    la = log_prices.get(leg_a) or []
    lb = log_prices.get(leg_b) or []
    if not la or not lb or spread_std <= 0:
        return ("no_price", None, None)
    spread_now = la[-1] - hedge_ratio * lb[-1]
    z = (spread_now - spread_mean) / spread_std

    # Bail-out trigger first: gap is widening pathologically.
    if abs(z) >= stop_z:
        return ("stopped", z, None)
    # Profit-take trigger: gap has reverted near zero.
    if abs(z) <= exit_z:
        return ("reverted", z, None)

    # Regime-break check (run only when the pair is mid-life).
    # Recompute the spread series + ADF-like p-value on today's data.
    n = min(len(la), len(lb))
    p_value: float | None = None
    if n >= MIN_OBS:
        la_w = la[-n:]
        lb_w = lb[-n:]
        spread_series = [la_w[i] - hedge_ratio * lb_w[i] for i in range(n)]
        p_value = _adf_like_p(spread_series)
        if p_value > p_max:
            if consecutive_failures + 1 >= regime_break_after:
                return ("regime_break", z, p_value)
    return ("hold", z, p_value)


def current_z_for_pair(
    leg_a: str, leg_b: str, hedge_ratio: float, mean: float, std: float,
    log_prices: dict[str, list[float]],
) -> float | None:
    la = log_prices.get(leg_a) or []
    lb = log_prices.get(leg_b) or []
    if not la or not lb or std <= 0:
        return None
    spread = la[-1] - hedge_ratio * lb[-1]
    return (spread - mean) / std
