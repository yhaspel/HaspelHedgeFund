"""Metrics + walk-forward deflation diagnostics.

All ratios assume daily returns and annualize with sqrt(252) for vol /
252 for return. Risk-free rate is assumed zero (P2c scope)."""
from __future__ import annotations

import math
import statistics
from decimal import Decimal

from apps.data.models import DailyBar

from .models import BacktestDay

TRADING_DAYS_PER_YEAR = 252


def sharpe_ratio(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    mean = statistics.fmean(returns)
    sd = statistics.pstdev(returns)
    if sd == 0:
        return 0.0
    return (mean / sd) * math.sqrt(TRADING_DAYS_PER_YEAR)


def sortino_ratio(returns: list[float], target: float = 0.0) -> float:
    if len(returns) < 2:
        return 0.0
    downside = [min(0.0, r - target) for r in returns]
    dsq = math.sqrt(statistics.fmean([d * d for d in downside]))
    if dsq == 0:
        return 0.0
    mean = statistics.fmean(returns)
    return ((mean - target) / dsq) * math.sqrt(TRADING_DAYS_PER_YEAR)


def drawdown_pct(equity: list[float]) -> float:
    """Max peak-to-trough drawdown, returned as a positive fraction (0..1).

    A non-positive equity value means the account is wiped: that is treated as a
    full 100% drawdown rather than letting the (peak - v)/peak term exceed 1.0,
    which would otherwise surface as a physically-impossible >100% drawdown.
    """
    if not equity:
        return 0.0
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak if v > 0 else 1.0
            dd = min(1.0, dd)
            if dd > max_dd:
                max_dd = dd
    return max_dd


def calmar_ratio(equity: list[float]) -> float:
    if len(equity) < 2:
        return 0.0
    total_ret = equity[-1] / equity[0] - 1.0
    years = len(equity) / TRADING_DAYS_PER_YEAR
    if years <= 0:
        return 0.0
    cagr = (1 + total_ret) ** (1 / years) - 1
    dd = drawdown_pct(equity)
    if dd == 0:
        return 0.0
    return cagr / dd


def hit_rate(returns: list[float]) -> float:
    if not returns:
        return 0.0
    wins = sum(1 for r in returns if r > 0)
    return wins / len(returns)


def win_loss_ratio(returns: list[float]) -> float:
    wins = [r for r in returns if r > 0]
    losses = [-r for r in returns if r < 0]
    if not wins or not losses:
        return 0.0
    return statistics.fmean(wins) / statistics.fmean(losses)


# ---------------------------------------------------------------------------


def _step_returns(equity: list[float]) -> list[float]:
    """Per-step simple returns, guarded against non-positive equity.

    Once equity reaches <= 0 the account is terminated: the crossing step is
    capped at -100% and compounding stops, instead of emitting sign-flipped
    ratios across zero (e.g. 50 -> -10 would otherwise read as -120%). For a
    strictly-positive series this is identical to equity[i]/equity[i-1] - 1.
    """
    rets: list[float] = []
    for i in range(1, len(equity)):
        prev, cur = equity[i - 1], equity[i]
        if prev <= 0:
            break
        if cur <= 0:
            rets.append(-1.0)
            break
        rets.append(cur / prev - 1.0)
    return rets


def _stitched_oos_equity(bt) -> list[tuple]:
    """Return [(date, value), ...] for the stitched OOS curve.

    The portfolio is reset between folds, so we stitch by chaining cumulative
    returns: each fold contributes its return path; cross-fold ratio = 1.0
    (gap day inherits prior fold's ending value).
    """
    qs = (
        BacktestDay.objects.filter(backtest=bt, segment=BacktestDay.SEG_OOS)
        .order_by("date")
        .values_list("date", "portfolio_value", "fold_id")
    )
    base = float(bt.starting_cash)
    out: list[tuple] = []
    current_fold = None
    fold_start_value = base
    for d, val, fid in qs:
        val = float(val)
        if fid != current_fold:
            # New fold begins at fold_start_value (= last cumulative)
            if out:
                fold_start_value = out[-1][1]
            else:
                fold_start_value = base
            current_fold = fid
            initial = val  # this fold's initial portfolio_value
            ratio = val / initial if initial else 1.0
            out.append((d, fold_start_value * ratio))
            _fold_initial = initial
        else:
            ratio = val / _fold_initial if _fold_initial else 1.0
            out.append((d, fold_start_value * ratio))
    return out


def stitched_oos_returns(bt) -> tuple[list, list[float]]:
    points = _stitched_oos_equity(bt)
    dates = [p[0] for p in points]
    equity = [p[1] for p in points]
    rets = _step_returns(equity)
    return dates, equity, rets  # type: ignore[return-value]


def _tr_prices(tickers: list[str], dates: list) -> dict:
    """{date: {ticker: total-return price}} over the window, preferring
    ``adjusted_close`` (dividend-credited) and falling back to ``close`` for
    rows that predate the adjusted-close backfill."""
    rows = DailyBar.objects.filter(
        ticker__in=tickers, date__gte=dates[0], date__lte=dates[-1]
    ).values("ticker", "date", "close", "adjusted_close")
    by_date: dict = {}
    for r in rows:
        px = r["adjusted_close"] or r["close"]
        if px:
            by_date.setdefault(r["date"], {})[r["ticker"]] = float(px)
    return by_date


def baseline_curve(bt, dates: list) -> list[float]:
    """TOTAL-RETURN equal-weighted basket of bt.universe OR SPY, normalized to
    bt.starting_cash.

    P10 §B1: chains the mean of per-ticker daily returns on ``adjusted_close``
    — a true (daily-rebalanced) equal-weight, dividend-credited basket. The
    previous implementation normalized the average of raw ``close`` levels,
    which was both price-only (dividend-less, while the strategy curve has
    credited dividends since PR #50) and Dow-style price-weighted (dominated by
    the highest-priced ticker). Tickers missing a bar on a given day simply
    don't contribute that day's return (carry, not liquidation).
    """
    if not dates:
        return []
    tickers = ["SPY"] if bt.baseline == "spy" else list(bt.universe)
    by_date = _tr_prices(tickers, dates)
    base = float(bt.starting_cash)
    out: list[float] = []
    last_px: dict[str, float] = {}
    value = base
    for i, d in enumerate(dates):
        prices = by_date.get(d, {})
        if i > 0:
            rets = [
                px / last_px[t] - 1.0
                for t, px in prices.items()
                if last_px.get(t, 0.0) > 0 and px > 0
            ]
            if rets:
                value *= 1.0 + sum(rets) / len(rets)
        last_px.update({t: px for t, px in prices.items() if px > 0})
        out.append(value)
    return out


# ---------------------------------------------------------------------------
# P10 §B2 — benchmark-relative truth (SPY-TR / QQQ-TR).
# ---------------------------------------------------------------------------

BENCHMARK_TICKERS = ("SPY", "QQQ")


def benchmark_curve(ticker: str, dates: list, base: float) -> list[float]:
    """Single-ticker total-return curve aligned to ``dates``, normalized to
    ``base``. Missing bars carry the last price forward. Returns [] when the
    ticker has no bars in the window (caller omits the overlay)."""
    if not dates:
        return []
    by_date = _tr_prices([ticker], dates)
    px_by_date = {d: m[ticker] for d, m in by_date.items() if ticker in m}
    if not px_by_date:
        return []
    out: list[float] = []
    anchor: float | None = None
    last: float | None = None
    for d in dates:
        px = px_by_date.get(d, last)
        if px is None:  # leading gap before the ticker's first bar
            out.append(base)
            continue
        if anchor is None:
            anchor = px
        last = px
        out.append(base * (px / anchor))
    return out


def _compound_blocks(rets: list[float], block: int) -> list[float]:
    """Compound per-step returns into non-overlapping `block`-step returns
    (trailing remainder dropped so both series block identically)."""
    out: list[float] = []
    for i in range(0, len(rets) - len(rets) % block, block):
        c = 1.0
        for r in rets[i : i + block]:
            c *= 1.0 + r
        out.append(c - 1.0)
    return out


# Engine-stored equity curves mark to the prior close, so a stitched OOS curve
# lags same-date benchmark bars by ~one session (audit-verified: lag+1 corr
# 0.537 vs 0.013 contemporaneous). A naive daily regression therefore collapses
# beta to ~0 and lets alpha absorb the whole market return. Compounding into
# 5-step blocks before regressing absorbs a ±1-session offset without assuming
# its direction; we only block when the series is long enough for the blocked
# estimate to be meaningful.
_BETA_BLOCK_DAYS = 5
_BETA_MIN_BLOCKS = 12


def beta_alpha_ir(strat_rets: list[float], bench_rets: list[float]) -> dict | None:
    """CAPM beta / annualized alpha (pp/yr) / information ratio of the strategy
    vs the benchmark, from aligned per-step (daily) simple returns. Risk-free
    rate 0 (consistent with sharpe_ratio). Long series are block-compounded to
    weekly steps first (see _BETA_BLOCK_DAYS note) so the engine's ±1-session
    curve/benchmark timestamp offset cannot zero-out beta."""
    n = min(len(strat_rets), len(bench_rets))
    if n < 2:
        return None
    s, b = strat_rets[-n:], bench_rets[-n:]
    periods_per_year = float(TRADING_DAYS_PER_YEAR)
    if n >= _BETA_BLOCK_DAYS * _BETA_MIN_BLOCKS:
        s = _compound_blocks(s, _BETA_BLOCK_DAYS)
        b = _compound_blocks(b, _BETA_BLOCK_DAYS)
        periods_per_year = TRADING_DAYS_PER_YEAR / _BETA_BLOCK_DAYS
        n = len(s)
    ms, mb = statistics.fmean(s), statistics.fmean(b)
    var_b = statistics.fmean([(x - mb) ** 2 for x in b])
    if var_b <= 0:
        return None
    cov = statistics.fmean([(s[i] - ms) * (b[i] - mb) for i in range(n)])
    beta = cov / var_b
    alpha_annual_pct = (ms - beta * mb) * periods_per_year * 100.0
    diffs = [s[i] - b[i] for i in range(n)]
    sd = statistics.pstdev(diffs)
    ir = (statistics.fmean(diffs) / sd) * math.sqrt(periods_per_year) if sd > 0 else 0.0
    return {
        "beta": round(beta, 4),
        "alpha_annual_pct": round(alpha_annual_pct, 4),
        "information_ratio": round(ir, 4),
    }


def benchmark_stats(bt, dates: list, equity: list[float]) -> dict:
    """Per-benchmark (SPY-TR / QQQ-TR) comparison block for BacktestMetrics:

        {"SPY": {total_return_pct, annualized_return_pct, sharpe,
                 max_drawdown_pct, beta, alpha_annual_pct, information_ratio},
         "QQQ": {...}}

    Benchmarks with no bar data in the window are omitted (the UI hides them).
    All ``_pct`` values are already in percent.
    """
    if len(dates) < 2 or len(equity) < 2:
        return {}
    strat_rets = _step_returns(equity)
    years = max(1e-9, len(equity) / TRADING_DAYS_PER_YEAR)
    out: dict = {}
    for ticker in BENCHMARK_TICKERS:
        curve = benchmark_curve(ticker, dates, float(equity[0]))
        if not curve:
            continue
        b_rets = _step_returns(curve)
        total = curve[-1] / curve[0] - 1.0
        cagr = ((1 + total) ** (1 / years) - 1) if total > -1 else -1.0
        rel = beta_alpha_ir(strat_rets, b_rets)
        out[ticker] = {
            "total_return_pct": round(total * 100, 4),
            "annualized_return_pct": round(cagr * 100, 4),
            "sharpe": round(sharpe_ratio(b_rets), 4),
            "max_drawdown_pct": round(drawdown_pct(curve) * 100, 4),
            **(rel or {}),
        }
    return out


def rolling_sharpe_series(
    dates: list, equity: list[float], *, window: int = 756, step: int = 21,
) -> list[dict]:
    """Rolling ``window``-day (default ~3y) Sharpe of the stitched OOS curve,
    sampled every ``step`` days — the detail-page sparkline (P10 §B2)."""
    rets = _step_returns(equity)
    if len(rets) < window:
        return []
    out: list[dict] = []
    for end in range(window, len(rets) + 1, step):
        sh = sharpe_ratio(rets[end - window:end])
        # rets[j] is the return INTO dates[j+1].
        out.append({"date": dates[end].isoformat(), "sharpe": round(sh, 4)})
    return out


def compute_stitched_metrics(bt, fold_records, agent_outputs_cache=None) -> dict:
    dates_eq = _stitched_oos_equity(bt)
    dates = [p[0] for p in dates_eq]
    equity = [p[1] for p in dates_eq]
    rets = _step_returns(equity)

    total_ret = (equity[-1] / equity[0] - 1.0) if len(equity) >= 2 else 0.0
    years = max(1e-9, len(equity) / TRADING_DAYS_PER_YEAR)
    cagr = ((1 + total_ret) ** (1 / years) - 1) if total_ret > -1 else -1.0
    dd = drawdown_pct(equity)
    sh = sharpe_ratio(rets)
    so = sortino_ratio(rets)
    hr = hit_rate(rets)
    wl = win_loss_ratio(rets)
    bl = baseline_curve(bt, dates)
    bl_ret = (bl[-1] / bl[0] - 1.0) if len(bl) >= 2 else 0.0

    is_s = [float(f.is_sharpe) for f in fold_records if f.is_sharpe is not None]
    oos_s = [float(f.oos_sharpe) for f in fold_records if f.oos_sharpe is not None]
    mean_is = statistics.fmean(is_s) if is_s else 0.0
    mean_oos = statistics.fmean(oos_s) if oos_s else 0.0
    # IS→OOS haircut. Divide by |mean_is| so the ratio keeps the SIGN of the OOS
    # Sharpe: the old `mean_oos / mean_is` was sign-blind, and a strategy that
    # was worse out of sample than in sample with both negative (IS -0.5, OOS
    # -2.0) reported "4x better OOS" and cleared the red-flag check. 1.0 still
    # means "OOS held up", < 0.3 is still the red flag, and a negative OOS
    # Sharpe now always lands below it.
    deflation = (mean_oos / abs(mean_is)) if mean_is != 0 else 0.0
    oos_std = statistics.pstdev(oos_s) if len(oos_s) >= 2 else 0.0

    # Turnover: |fill notional|/equity per day, summed and annualized.
    # Reads from BacktestDay.fills (executed) — not decisions (intent) — so
    # the metric is auditable and matches what actually moved capital.
    qs = BacktestDay.objects.filter(backtest=bt, segment=BacktestDay.SEG_OOS).order_by("date")
    daily_turnover = 0.0
    eq_for_to = float(bt.starting_cash)
    for day in qs:
        fills = day.fills or []
        notional = sum(abs(f.get("notional", 0.0)) for f in fills)
        if eq_for_to > 0:
            daily_turnover += notional / eq_for_to
        eq_for_to = float(day.portfolio_value)
    turnover_pct = (daily_turnover / max(1, len(qs))) * TRADING_DAYS_PER_YEAR * 100.0

    return {
        "total_return_pct": Decimal(str(round(total_ret * 100, 4))),
        "annualized_return_pct": Decimal(str(round(cagr * 100, 4))),
        "sharpe": Decimal(str(round(sh, 4))),
        "sortino": Decimal(str(round(so, 4))),
        "max_drawdown_pct": Decimal(str(round(dd * 100, 4))),
        "hit_rate": Decimal(str(round(hr, 4))),
        "win_loss_ratio": Decimal(str(round(wl, 4))),
        "turnover_pct": Decimal(str(round(turnover_pct, 4))),
        "mean_is_sharpe": Decimal(str(round(mean_is, 4))),
        "mean_oos_sharpe": Decimal(str(round(mean_oos, 4))),
        "sharpe_deflation": Decimal(str(round(deflation, 4))),
        "oos_sharpe_std": Decimal(str(round(oos_std, 4))),
        "baseline_return_pct": Decimal(str(round(bl_ret * 100, 4))),
        # P10 §B2: SPY-TR/QQQ-TR comparison (beta / CAPM alpha / IR). {} when
        # benchmark bars are absent (e.g. unit-test fixtures).
        "benchmarks": benchmark_stats(bt, dates, equity),
    }
