"""Metrics + walk-forward deflation diagnostics.

All ratios assume daily returns and annualize with sqrt(252) for vol /
252 for return. Risk-free rate is assumed zero (P2c scope)."""
from __future__ import annotations

import math
import statistics
from decimal import Decimal

from apps.data.models import DailyBar

from .engine import trading_days
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
    """Max peak-to-trough drawdown, returned as a positive fraction (0..1)."""
    if not equity:
        return 0.0
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
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
    fold_running = base
    for d, val, fid in qs:
        val = float(val)
        if fid != current_fold:
            # New fold begins at fold_start_value (= last cumulative)
            if out:
                fold_start_value = out[-1][1]
            else:
                fold_start_value = base
            current_fold = fid
            fold_running = fold_start_value
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
    rets = [(equity[i] / equity[i - 1]) - 1.0 for i in range(1, len(equity))] if len(equity) >= 2 else []
    return dates, equity, rets  # type: ignore[return-value]


def baseline_curve(bt, dates: list) -> list[float]:
    """Equal-weighted basket of bt.universe OR SPY, normalized to bt.starting_cash."""
    if not dates:
        return []
    tickers = ["SPY"] if bt.baseline == "spy" else list(bt.universe)
    rows = DailyBar.objects.filter(
        ticker__in=tickers, date__gte=dates[0], date__lte=dates[-1]
    ).values("ticker", "date", "close")
    by_date: dict = {}
    for r in rows:
        by_date.setdefault(r["date"], {})[r["ticker"]] = float(r["close"])
    base = float(bt.starting_cash)
    out: list[float] = []
    initial_avg = None
    for d in dates:
        prices = by_date.get(d, {})
        present = [prices[t] for t in tickers if t in prices and prices[t] > 0]
        if not present:
            out.append(out[-1] if out else base)
            continue
        avg = sum(present) / len(present)
        if initial_avg is None:
            initial_avg = avg
        out.append(base * (avg / initial_avg))
    return out


def compute_stitched_metrics(bt, fold_records, agent_outputs_cache=None) -> dict:
    dates_eq = _stitched_oos_equity(bt)
    dates = [p[0] for p in dates_eq]
    equity = [p[1] for p in dates_eq]
    rets = [(equity[i] / equity[i - 1]) - 1.0 for i in range(1, len(equity))] if len(equity) >= 2 else []

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
    deflation = (mean_oos / mean_is) if mean_is != 0 else 0.0
    oos_std = statistics.pstdev(oos_s) if len(oos_s) >= 2 else 0.0

    # Turnover: |notional|/equity per day, summed and annualized.
    qs = BacktestDay.objects.filter(backtest=bt, segment=BacktestDay.SEG_OOS).order_by("date")
    daily_turnover = 0.0
    eq_for_to = float(bt.starting_cash)
    for day in qs:
        notional = sum(abs(f.get("notional", 0.0)) for f in (day.decisions or []))
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
    }
