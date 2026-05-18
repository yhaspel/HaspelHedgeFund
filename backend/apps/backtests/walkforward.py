"""Walk-forward orchestrator.

Generates rolling (IS, OOS) folds, primes the agent-output cache once over
the full master window, then per fold: runs the IS optimizer, picks the
winning config, replays OOS with that config, and persists per-fold state.

After all folds finish, the stitched OOS equity curve is materialized into
`BacktestDay` rows tagged segment="oos".
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from decimal import Decimal

from django.utils import timezone

from hedgefund_agents.personas import ALL_PERSONAS

from .engine import (
    SegmentResult,
    prime_agent_cache,
    rebalance_dates_for,
    run_segment,
    trading_days,
)
from .models import Backtest, BacktestDay, BacktestFold, BacktestMetrics
from .optimizer import optimize_is

log = logging.getLogger(__name__)


@dataclass
class Fold:
    index: int
    is_start: dt.date
    is_end: dt.date
    oos_start: dt.date
    oos_end: dt.date


def generate_folds(
    *, start: dt.date, end: dt.date,
    is_window_days: int, oos_window_days: int, step_days: int,
) -> list[Fold]:
    """Non-overlapping OOS by default (step = oos_window_days)."""
    folds: list[Fold] = []
    cur = start + dt.timedelta(days=is_window_days)
    i = 0
    while cur + dt.timedelta(days=oos_window_days) <= end:
        is_end = cur - dt.timedelta(days=1)
        is_start = cur - dt.timedelta(days=is_window_days)
        oos_start = cur
        oos_end = cur + dt.timedelta(days=oos_window_days - 1)
        folds.append(
            Fold(
                index=i, is_start=is_start, is_end=is_end,
                oos_start=oos_start, oos_end=oos_end,
            )
        )
        i += 1
        cur += dt.timedelta(days=step_days)
    return folds


def _persist_segment_days(
    *, bt: Backtest, fold: BacktestFold, segment: str, seg: SegmentResult,
) -> None:
    rows = []
    for i, d in enumerate(seg.dates):
        rows.append(BacktestDay(
            backtest=bt, fold=fold, segment=segment, date=d,
            cash=Decimal(str(round(seg.cash[i], 2))),
            positions=seg.positions_by_day[i],
            portfolio_value=Decimal(str(round(seg.equity[i], 2))),
            decisions=seg.decisions_by_day[i] if i < len(seg.decisions_by_day) else [],
        ))
    BacktestDay.objects.bulk_create(rows)


def _save_progress(bt: Backtest, pct: int, msg: str) -> None:
    Backtest.objects.filter(pk=bt.pk).update(progress_pct=pct, progress_message=msg[:200])


def run_walkforward(bt: Backtest) -> None:
    bt.status = Backtest.RUNNING
    bt.started_at = timezone.now()
    bt.save(update_fields=["status", "started_at"])
    _save_progress(bt, 1, "generating folds")

    folds = generate_folds(
        start=bt.start_date, end=bt.end_date,
        is_window_days=bt.is_window_days,
        oos_window_days=bt.oos_window_days,
        step_days=bt.step_days,
    )
    if not folds:
        bt.status = Backtest.FAILED
        bt.error_message = (
            f"No folds fit in [{bt.start_date} .. {bt.end_date}] "
            f"with is_window={bt.is_window_days} oos_window={bt.oos_window_days}. "
            "Try a wider master range or shorter IS/OOS windows."
        )
        bt.finished_at = timezone.now()
        bt.save(update_fields=["status", "error_message", "finished_at"])
        return

    _save_progress(bt, 2, f"priming agent cache for {len(bt.universe)} tickers × master window")

    def progress(done: int, total: int, note: str) -> None:
        pct = min(60, 2 + int(done / max(1, total) * 58))
        _save_progress(bt, pct, note)

    agent_outputs_cache = prime_agent_cache(
        bt=bt,
        start=bt.start_date,
        end=bt.end_date,
        rebalance_freq=bt.rebalance_frequency,
        progress_cb=progress,
    )

    personas = list(bt.personas or ALL_PERSONAS)

    fold_records: list[BacktestFold] = []
    for f_idx, fold in enumerate(folds):
        pct = 60 + int((f_idx + 1) / len(folds) * 35)
        _save_progress(bt, pct, f"fold {f_idx+1}/{len(folds)}: IS sweep")

        candidates = optimize_is(
            bt=bt, is_start=fold.is_start, is_end=fold.is_end,
            agent_outputs_cache=agent_outputs_cache,
            personas=personas, n_candidates=bt.n_candidates,
        )
        winner = candidates[0]
        # OOS evaluation
        oos_days = trading_days(fold.oos_start, fold.oos_end, list(bt.universe))
        oos_rebal = rebalance_dates_for(oos_days, bt.rebalance_frequency)
        oos_seg = run_segment(
            bt=bt, start=fold.oos_start, end=fold.oos_end,
            pm_config=winner.config,
            agent_outputs_cache=agent_outputs_cache,
            rebalance_dates=oos_rebal,
        )
        from .metrics import drawdown_pct, sharpe_ratio

        rets = (
            [(oos_seg.equity[i] / oos_seg.equity[i - 1]) - 1.0
             for i in range(1, len(oos_seg.equity))]
            if len(oos_seg.equity) >= 2 else []
        )
        oos_sharpe = sharpe_ratio(rets)
        oos_ret = (oos_seg.equity[-1] / oos_seg.equity[0] - 1.0) * 100 if oos_seg.equity else 0
        oos_dd = drawdown_pct(oos_seg.equity) * 100 if oos_seg.equity else 0

        rec = BacktestFold.objects.create(
            backtest=bt, fold_index=fold.index,
            is_start=fold.is_start, is_end=fold.is_end,
            oos_start=fold.oos_start, oos_end=fold.oos_end,
            winning_config=winner.config,
            is_sharpe=Decimal(str(round(winner.score, 4))),
            oos_sharpe=Decimal(str(round(oos_sharpe, 4))),
            oos_return_pct=Decimal(str(round(oos_ret, 4))),
            oos_max_drawdown_pct=Decimal(str(round(oos_dd, 4))),
            candidates_scored=[
                {"score": round(c.score, 4), "return_pct": round(c.return_pct, 4)}
                for c in candidates
            ],
        )
        _persist_segment_days(bt=bt, fold=rec, segment=BacktestDay.SEG_OOS, seg=oos_seg)
        fold_records.append(rec)

    _save_progress(bt, 96, "computing metrics + attribution")
    from .attribution import compute_attribution
    from .metrics import compute_stitched_metrics

    metrics = compute_stitched_metrics(bt, fold_records, agent_outputs_cache)
    BacktestMetrics.objects.update_or_create(backtest=bt, defaults=metrics)
    compute_attribution(bt, fold_records, agent_outputs_cache, personas)

    bt.status = Backtest.DONE
    bt.finished_at = timezone.now()
    _save_progress(bt, 100, "done")
    bt.save(update_fields=["status", "finished_at"])
