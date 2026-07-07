"""P10 §B5 — the fund-level composite view: what do the pods do TOGETHER?

Read-only over existing ``BacktestDay`` data — no engine work. Each fund member
strategy contributes its **record of record** (latest real ``done``,
total-return-era validation backtest); curves are the stitched OOS equity
paths, each normalized to 1.0 at the common start, combined at configurable
weights (default equal — the live fund's 33/33/33 capital split, with each pod
compounding independently), then compared against SPY-TR / QQQ-TR over the
identical window with beta / CAPM alpha / IR.

The honest framing this enables (P10 §G): the fund's edge is risk-adjusted
(composite Sharpe ≈ 1.0 vs SPY ≈ 0.6 at ~¼ the drawdown, CAPM α +4–5%/yr at
beta ≈ 0.2) while trailing SPY/QQQ on raw return. Both halves render.
"""
from __future__ import annotations

import datetime as dt
import math
import statistics

from apps.backtests.metrics import (
    TRADING_DAYS_PER_YEAR,
    _step_returns,
    benchmark_curve,
    beta_alpha_ir,
    drawdown_pct,
    sharpe_ratio,
    stitched_oos_returns,
)
from apps.backtests.models import Backtest

COMPOSITE_BASE = 100.0
# P11 A3 — the research leans on a post-GFC (2010–) sub-period to control for the
# 2006–09 bond-bull tailwind, and a "Core ×1.5" leverage view (BAB: spend the
# Sharpe surplus on return). Both render off the same stored OOS curves.
POST_GFC_START = dt.date(2010, 1, 1)
DEFAULT_FINANCING_BPS = 200.0  # ~2%/yr; matches Backtest.financing_bps (E2)


def _lever_curve(curve: list[float], leverage: float, financing_bps: float) -> list[float]:
    """Re-lever a normalized equity curve to ``leverage``× market risk, net of the
    ``(L-1)·rf`` financing drag charged on the borrow — the same model the engine
    applies daily on a gross>1 book (P11 E2), so composite and pod agree. ``L=1``
    is a no-op; the benchmarks are never levered."""
    if leverage == 1.0 or len(curve) < 2:
        return curve
    fin_daily = max(0.0, financing_bps) / 10_000.0 / TRADING_DAYS_PER_YEAR
    borrow = max(0.0, leverage - 1.0)
    out = [curve[0]]
    for r in _step_returns(curve):
        out.append(out[-1] * (1.0 + leverage * r - borrow * fin_daily))
    return out


def _calendar_years(grid: list, series: dict[str, list[float]]) -> list[dict]:
    """Per-calendar-year total return (%) for each labelled curve — the 2008/2022
    crisis rows and every-bull-year-lag the research leans on."""
    by_year: dict[int, list[int]] = {}
    for i, d in enumerate(grid):
        by_year.setdefault(d.year, []).append(i)
    rows: list[dict] = []
    for year in sorted(by_year):
        idx = by_year[year]
        lo, hi = idx[0], idx[-1]
        row = {"year": year}
        for label, vals in series.items():
            row[label] = (
                round((vals[hi] / vals[lo] - 1.0) * 100, 2)
                if vals[lo] else None
            )
        rows.append(row)
    return rows


def record_of_record(strategy) -> Backtest | None:
    """The strategy's latest real (done, total-return-era) backtest — the same
    selection rule as the §9 validation gate."""
    return (
        Backtest.objects.filter(
            strategy=strategy,
            status=Backtest.DONE,
            data_era=Backtest.ERA_TOTAL_RETURN,
        )
        .order_by("-created_at")
        .first()
    )


def _series_stats(dates: list, values: list[float]) -> dict:
    """Ann return / vol / Sharpe / maxDD block for one curve (daily points)."""
    rets = _step_returns(values)
    total = (values[-1] / values[0] - 1.0) if len(values) >= 2 and values[0] else 0.0
    years = max(1e-9, len(values) / TRADING_DAYS_PER_YEAR)
    cagr = ((1 + total) ** (1 / years) - 1) if total > -1 else -1.0
    vol = statistics.pstdev(rets) * math.sqrt(TRADING_DAYS_PER_YEAR) if len(rets) >= 2 else 0.0
    return {
        "total_return_pct": round(total * 100, 2),
        "annualized_return_pct": round(cagr * 100, 2),
        "vol_annual_pct": round(vol * 100, 2),
        "sharpe": round(sharpe_ratio(rets), 3),
        "max_drawdown_pct": round(drawdown_pct(values) * 100, 2),
    }


def _parse_weights(raw: str | None, strategy_ids: list[int]) -> dict[int, float] | None:
    """``"53:0.6,54:0.2,55:0.2"`` → normalized {id: weight}; None on bad input
    or ids that aren't fund members (caller falls back to equal weight)."""
    if not raw:
        return None
    out: dict[int, float] = {}
    try:
        for part in raw.split(","):
            sid, w = part.split(":")
            out[int(sid.strip())] = float(w)
    except (ValueError, AttributeError):
        return None
    if set(out) != set(strategy_ids) or any(w < 0 for w in out.values()):
        return None
    z = sum(out.values())
    if z <= 0:
        return None
    return {sid: w / z for sid, w in out.items()}


def fund_composite(
    fund, *, weights_raw: str | None = None, leverage: float = 1.0,
    financing_bps: float = DEFAULT_FINANCING_BPS, sub_period: str = "full",
) -> dict:
    """The §B5 composite payload for ``GET /api/fund/composite/``.

    P11 A3: ``leverage`` re-levers the composite toward market risk net of a
    ``(L-1)·rf`` financing drag (default 2%/yr, matching the engine's E2 drag);
    ``sub_period="post_gfc"`` clips the window to 2010– (controls for the bond-bull
    tailwind); the payload always carries a per-calendar-year return table. All
    three read the same stored OOS curves — no engine work, benchmarks unlevered.
    """
    leverage = max(0.0, float(leverage or 1.0))
    if financing_bps is None:
        financing_bps = DEFAULT_FINANCING_BPS
    financing_bps = max(0.0, float(financing_bps))
    strategies = list(fund.strategies.all())
    members: list[dict] = []
    curves: dict[int, tuple[list, list[float]]] = {}
    missing: list[str] = []
    for s in strategies:
        bt = record_of_record(s)
        if bt is None:
            missing.append(s.name)
            continue
        dates, equity, _ = stitched_oos_returns(bt)
        if len(dates) < 2:
            missing.append(s.name)
            continue
        curves[s.id] = (dates, equity)
        members.append({
            "strategy_id": s.id,
            "name": s.name,
            "kind": s.kind,
            "backtest_id": bt.id,
            "backtest_name": bt.name,
        })
    if not curves:
        return {
            "available": False,
            "reason": "no member strategy has a total-return-era validation backtest",
            "missing": missing,
        }

    ids = [m["strategy_id"] for m in members]
    weights = _parse_weights(weights_raw, ids) or {sid: 1.0 / len(ids) for sid in ids}
    for m in members:
        m["weight"] = round(weights[m["strategy_id"]], 4)

    # Common grid: union of dates from the latest common start (every member
    # present), carry-forward between each member's own points. P11 A3: the
    # post-GFC toggle clips the start to 2010–, re-anchoring the curves there.
    starts = [dates[0] for dates, _ in curves.values()]
    start = max(starts)
    if sub_period == "post_gfc":
        start = max(start, POST_GFC_START)
    grid = sorted({d for dates, _ in curves.values() for d in dates if d >= start})
    if len(grid) < 2:
        return {"available": False, "reason": "no overlapping OOS window", "missing": missing}

    norm: dict[int, list[float]] = {}
    for sid, (dates, equity) in curves.items():
        lookup = dict(zip(dates, equity, strict=False))
        vals: list[float] = []
        last = None
        for d in grid:
            v = lookup.get(d, last)
            if v is None:  # before this member's first point (can't happen post-start)
                v = equity[0]
            last = v
            vals.append(v)
        anchor = vals[0] or 1.0
        norm[sid] = [v / anchor for v in vals]

    composite = [
        COMPOSITE_BASE * sum(weights[sid] * norm[sid][i] for sid in norm)
        for i in range(len(grid))
    ]
    # P11 A3 — re-lever the composite (benchmarks stay 1×). Net of the (L-1)·rf
    # drag so it agrees with the engine's per-pod financing (E2).
    composite = _lever_curve(composite, leverage, financing_bps)
    bench = {
        t: c for t in ("SPY", "QQQ")
        if (c := benchmark_curve(t, grid, COMPOSITE_BASE))
    }

    comp_rets = _step_returns(composite)
    metrics: dict[str, dict] = {"composite": _series_stats(grid, composite)}
    for t, curve in bench.items():
        metrics[t] = _series_stats(grid, curve)
        rel = beta_alpha_ir(comp_rets, _step_returns(curve))
        if rel:
            metrics["composite"][f"vs_{t.lower()}"] = rel

    points = [
        {
            "date": d.isoformat(),
            "composite": round(composite[i], 2),
            **{t.lower(): round(curve[i], 2) for t, curve in bench.items()},
        }
        for i, d in enumerate(grid)
    ]
    calendar_series = {"composite": composite, **{t.lower(): c for t, c in bench.items()}}
    return {
        "available": True,
        "members": members,
        "missing": missing,
        "window": {"start": grid[0].isoformat(), "end": grid[-1].isoformat()},
        "base": COMPOSITE_BASE,
        "leverage": round(leverage, 4),
        "financing_bps": round(financing_bps, 2),
        "sub_period": "post_gfc" if sub_period == "post_gfc" else "full",
        "points": points,
        "metrics": metrics,
        "calendar_years": _calendar_years(grid, calendar_series),
    }
