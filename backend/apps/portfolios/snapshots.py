"""P10 §C2/§C3/§C4 — NAV history: persist, flow-adjust, serve.

``record_snapshot`` upserts one ``PortfolioSnapshot`` per (portfolio, date) —
called from the hourly ``guardrail_sweep`` (which already computed the marked
equity and used to throw it away) and from the §C3 broker-history backfill.

``net_flow`` (external cash in/out on the date) is derived from the book's
ledger: deposits, withdrawals and reconciliation adjustments are EXTERNAL to
performance (a funding transfer surfaces as a reconciliation cash adjustment);
fills and position legs are internal (cash ↔ positions). Returns are
**time-weighted**: r_t = (E_t − F_t) / E_{t−1} − 1, so the planned F1 capital
tilt (±$25K+ transfers) and the acct-13 $1M→$100K funding reset never print as
performance.

``fund_history`` builds the ``GET /api/fund/history/`` payload: per-account +
aggregate series (raw equity AND a base-100 TWR index) + normalized SPY/QQQ
from DailyBar.
"""
from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal

from django.db.models import Sum

from .models import LedgerEntry, Portfolio, PortfolioSnapshot

log = logging.getLogger(__name__)

# Ledger kinds that are EXTERNAL cash flows (not performance). Trading kinds
# (broker_fill, position_open/…) move value between cash and positions and
# must NOT be excluded from returns.
EXTERNAL_FLOW_KINDS = (
    LedgerEntry.KIND_DEPOSIT,
    LedgerEntry.KIND_WITHDRAWAL,
    LedgerEntry.KIND_RECONCILE,
)

INDEX_BASE = 100.0


def external_flow(portfolio: Portfolio, on: dt.date) -> Decimal:
    """Net external cash flow (deposits +, withdrawals −) booked on ``on``."""
    total = (
        LedgerEntry.objects.filter(
            portfolio=portfolio, kind__in=EXTERNAL_FLOW_KINDS,
            created_at__date=on,
        ).aggregate(s=Sum("cash_delta"))["s"]
    )
    return total or Decimal("0")


def record_snapshot(
    portfolio: Portfolio,
    *,
    equity,
    cash=None,
    on: dt.date | None = None,
    net_flow=None,
    source: str = PortfolioSnapshot.SOURCE_SWEEP,
) -> PortfolioSnapshot | None:
    """Upsert the (portfolio, date) snapshot — last write for the day wins
    (the hourly sweep converges on the post-close mark). ``net_flow`` defaults
    to the day's ledger-derived external flow. Never raises (history capture
    must not break the risk sweep)."""
    from django.utils import timezone

    try:
        on = on or timezone.localdate()
        eq = Decimal(str(equity))
        flow = Decimal(str(net_flow)) if net_flow is not None else external_flow(portfolio, on)
        snap, _created = PortfolioSnapshot.objects.update_or_create(
            portfolio=portfolio, date=on,
            defaults={
                "equity": eq,
                "cash": Decimal(str(cash)) if cash is not None else None,
                "net_flow": flow,
                "source": source,
            },
        )
        return snap
    except Exception:  # noqa: BLE001 — never break the caller (risk sweep)
        log.exception("record_snapshot failed portfolio=%s", portfolio.pk)
        return None


def twr_returns(points: list[dict]) -> list[float]:
    """Per-step time-weighted returns from [{date, equity, net_flow}, ...]:
    r_t = (E_t − F_t) / E_{t−1} − 1 (flows treated as arriving during day t)."""
    out: list[float] = []
    for i in range(1, len(points)):
        prev = float(points[i - 1]["equity"])
        cur = float(points[i]["equity"])
        flow = float(points[i].get("net_flow") or 0.0)
        if prev <= 0:
            out.append(0.0)
            continue
        out.append((cur - flow) / prev - 1.0)
    return out


def twr_index(points: list[dict], base: float = INDEX_BASE) -> list[float]:
    """Chained TWR index (base 100 at the first point) — the flow-immune curve
    that is honest to overlay against SPY/QQQ."""
    idx = [base]
    for r in twr_returns(points):
        idx.append(idx[-1] * (1.0 + r))
    return idx


def _series_for(portfolio: Portfolio, since: dt.date | None) -> list[dict]:
    qs = PortfolioSnapshot.objects.filter(portfolio=portfolio).order_by("date")
    if since:
        qs = qs.filter(date__gte=since)
    return [
        {
            "date": s.date,
            "equity": float(s.equity),
            "net_flow": float(s.net_flow),
        }
        for s in qs
    ]


def _benchmark_index(ticker: str, dates: list[dt.date]) -> list[float] | None:
    """SPY/QQQ total-return index normalized to INDEX_BASE on the grid (carry-
    forward), or None when the ticker has no bars in the window."""
    from apps.backtests.metrics import benchmark_curve

    curve = benchmark_curve(ticker, dates, INDEX_BASE)
    return curve or None


def fund_history(fund, *, days: int | None = None) -> dict:
    """The §C2 payload: per-account + aggregate equity history with TWR, plus
    SPY/QQQ overlays. ``days`` limits the window (None = everything)."""
    from apps.brokers.models import StrategyBrokerLink

    from . import sleeves
    from .fund_composite import record_of_record

    since = (dt.date.today() - dt.timedelta(days=days)) if days else None
    strategies = [sl.strategy for sl in fund.active_sleeves()]
    per_account: list[dict] = []
    series_by_id: dict[int, list[dict]] = {}
    for s in strategies:
        # P14: a member's history is its SLEEVE's (its slice of the shared
        # account); a legacy stand-alone link falls back to the whole account.
        pf = sleeves.member_book(s)
        if pf is None:
            link = (
                StrategyBrokerLink.objects.filter(strategy=s, is_active=True)
                .select_related("broker_account__portfolio")
                .first()
            )
            pf = link.broker_account.portfolio if link else None
        points = _series_for(pf, since) if pf is not None else []
        series_by_id[s.id] = points
        idx = twr_index(points) if points else []
        # Realized-vs-expected strip (§C4): the validated annualized return
        # from the pod's record of record, next to the realized TWR.
        bt = record_of_record(s)
        expected = None
        if bt is not None:
            metrics = getattr(bt, "metrics", None)
            if metrics is not None:
                expected = float(metrics.annualized_return_pct)
        per_account.append({
            "strategy_id": s.id,
            "name": s.name,
            "kind": s.kind,
            "portfolio_id": pf.id if pf is not None else None,
            "points": [
                {**p, "date": p["date"].isoformat(), "index": round(idx[i], 4)}
                for i, p in enumerate(points)
            ],
            "twr_pct": round(idx[-1] - INDEX_BASE, 4) if len(idx) >= 2 else None,
            "expected_ann_return_pct": expected,
            "expected_backtest_id": bt.id if bt is not None else None,
        })

    # Aggregate: P14 — the shared ACCOUNT's own snapshot series is the truth
    # (it includes unallocated residue the sleeves don't claim). Fall back to
    # the union-of-members roll-up while the fund has no account, or the account
    # has fewer than two daily points (a one-point series carries no return —
    # at reset Σ sleeves == the account anyway, so the hand-over is seamless).
    acct_pf = sleeves.account_book(fund)
    acct_points = _series_for(acct_pf, since) if acct_pf is not None else []
    grid = sorted({p["date"] for pts in series_by_id.values() for p in pts})
    agg_points: list[dict] = []
    if len(acct_points) >= 2:
        # The account series defines the aggregate grid (benchmarks align to it).
        grid = [p["date"] for p in acct_points]
        agg_points = [dict(p) for p in acct_points]
    elif grid:
        last: dict[int, float] = {}
        by_date: dict[int, dict] = {
            sid: {p["date"]: p for p in pts} for sid, pts in series_by_id.items()
        }
        for d in grid:
            eq = 0.0
            flow = 0.0
            for sid in series_by_id:
                p = by_date[sid].get(d)
                if p is not None:
                    if sid not in last:
                        # A member entering the composite mid-grid (a clean-
                        # started backfill, or a pod added to the fund later)
                        # is CAPITAL joining the book, not performance — count
                        # its entire first-day equity as flow, or the aggregate
                        # TWR books the join as a fake gain.
                        flow += p["equity"]
                    else:
                        flow += p["net_flow"]
                    last[sid] = p["equity"]
                eq += last.get(sid, 0.0)
            agg_points.append({"date": d, "equity": round(eq, 2), "net_flow": round(flow, 2)})
    agg_idx = twr_index(agg_points) if agg_points else []

    benchmarks: dict[str, list[float]] = {}
    if grid:
        for t in ("SPY", "QQQ"):
            series = _benchmark_index(t, grid)
            if series:
                benchmarks[t] = series

    return {
        "available": bool(grid),
        "reason": None if grid else "no snapshots yet — history accrues from the "
                                    "hourly sweep, or run backfill_portfolio_history",
        "per_account": per_account,
        "aggregate": {
            "points": [
                {**p, "date": p["date"].isoformat(),
                 "index": round(agg_idx[i], 4),
                 **{t.lower(): round(benchmarks[t][i], 4) for t in benchmarks}}
                for i, p in enumerate(agg_points)
            ],
            "twr_pct": round(agg_idx[-1] - INDEX_BASE, 4) if len(agg_idx) >= 2 else None,
        },
        "benchmarks": sorted(benchmarks.keys()),
    }
