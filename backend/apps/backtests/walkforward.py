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
    DETERMINISTIC_DEFAULTS,
    SegmentResult,
    prime_agent_cache,
    rebalance_dates_for,
    run_deterministic_segment,
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
    """Non-overlapping OOS by default (step = oos_window_days).

    ``step_days`` must be positive: at 0 the ``cur`` cursor never advances and
    this loop appends folds forever (one Celery prefork slot pinned at 100% CPU
    until the worker OOMs); negative steps walk ``cur`` backwards until
    ``datetime.date`` underflows with OverflowError after ~100k folds. The
    create serializer rejects both, but this is the last line of defence for
    rows that reach the engine another way.
    """
    if step_days is None or step_days <= 0:
        raise ValueError(f"step_days must be >= 1 (got {step_days!r})")
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
            fills=seg.fills_by_day[i] if i < len(seg.fills_by_day) else [],
        ))
    BacktestDay.objects.bulk_create(rows)


def _save_progress(bt: Backtest, pct: int, msg: str) -> None:
    # heartbeat_at doubles as the orphan sweeper's liveness signal: a run that is
    # slow but writing progress is never swept.
    Backtest.objects.filter(pk=bt.pk).update(
        progress_pct=pct, progress_message=msg[:200], heartbeat_at=timezone.now(),
    )


class BacktestNotClaimable(RuntimeError):
    """The backtest was not QUEUED when the worker tried to claim it.

    Raised (and swallowed by ``tasks.run_backtest``) when a task executes for a
    row that has already reached a terminal state — a user cancel that raced the
    worker, or a task the orphan sweeper already failed. Without the claim the
    run would flip CANCELLED/FAILED → RUNNING → DONE, overwriting the terminal
    state and manufacturing §9-gate-eligible evidence out of a cancelled run.
    """


def _claim(bt: Backtest) -> bool:
    """Atomically move QUEUED → RUNNING. False if somebody else got there first
    (or the row is already terminal)."""
    claimed = Backtest.objects.filter(pk=bt.pk, status=Backtest.QUEUED).update(
        status=Backtest.RUNNING,
        started_at=timezone.now(),
        heartbeat_at=timezone.now(),
        engine_version=Backtest.ENGINE_VERSION,
        error_message="",
    )
    if not claimed:
        return False
    bt.refresh_from_db(
        fields=["status", "started_at", "heartbeat_at", "engine_version", "error_message"]
    )
    return True


def run_walkforward(bt: Backtest) -> None:
    if not _claim(bt):
        current = (
            Backtest.objects.filter(pk=bt.pk).values_list("status", flat=True).first()
        )
        log.warning(
            "run_walkforward: backtest %s is %s, not queued — refusing to run", bt.pk, current
        )
        raise BacktestNotClaimable(f"backtest {bt.pk} is {current}, not queued")
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

    if bt.engine_mode in Backtest.DETERMINISTIC_ENGINE_MODES:
        _run_deterministic_walkforward(bt, folds)
        return

    _save_progress(bt, 2, f"priming agent cache for {len(bt.universe)} tickers × master window")

    def progress(done: int, total: int, note: str) -> None:
        # Use round(), not int(): for a 20-name × 157-week master window
        # (total≈3140), int() truncation would keep pct stuck at 2 until ~55
        # ticker-days completed — hours of apparent no-progress in the UI
        # while the engine is actually working. round() advances on every ~27
        # ticker-days so the gauge moves smoothly. Also bump by 1 the instant
        # any work has happened so users see immediate liveness.
        raw = (done / max(1, total)) * 58
        pct = 2 + (max(1, round(raw)) if done > 0 else 0)
        _save_progress(bt, min(60, pct), note)

    agent_outputs_cache = prime_agent_cache(
        bt=bt,
        start=bt.start_date,
        end=bt.end_date,
        rebalance_freq=bt.rebalance_frequency,
        progress_cb=progress,
    )

    personas = list(bt.personas or ALL_PERSONAS)

    # Engine v2: ONE book carried across every OOS fold, exactly as the
    # deterministic path already did. Before this, run_segment built a fresh
    # SimulatedPortfolio per fold, so at each boundary the whole book was
    # liquidated and re-bought — full commission + spread on ~100% notional,
    # turnover inflated by the fold count, and the market move over each fold's
    # last session dropped from the stitched curve. Only fold 0 seeds from cash.
    from .portfolio import SimulatedPortfolio

    oos_pf = SimulatedPortfolio(
        starting_cash=float(bt.starting_cash),
        commission_bps=float(bt.commission_bps),
        spread_bps=float(bt.spread_bps),
        financing_bps=float(getattr(bt, "financing_bps", 0) or 0),
    )

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
            pf=oos_pf,
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


def _run_deterministic_walkforward(bt: Backtest, folds: list[Fold]) -> None:
    """Council-free walk-forward: deterministic inverse-vol (risk-parity) sizing.

    No prime, no IS optimization, no LLM. Each fold replays its OOS window with a
    fixed config (DETERMINISTIC_DEFAULTS overlaid with bt.search_space); is_sharpe
    is the in-sample Sharpe of the same config, for the IS→OOS comparison. Reuses
    the same fold/day persistence + stitched metrics as the council path.
    """
    from .metrics import compute_stitched_metrics, drawdown_pct, sharpe_ratio
    from .portfolio import SimulatedPortfolio

    config = {**DETERMINISTIC_DEFAULTS, **(bt.search_space or {})}
    bt.prime_completeness = 1.0
    bt.save(update_fields=["prime_completeness"])

    def _seg(start: dt.date, end: dt.date, pf=None) -> SegmentResult:
        days = trading_days(start, end, list(bt.universe))
        reb = rebalance_dates_for(days, bt.rebalance_frequency)
        return run_deterministic_segment(
            bt=bt, start=start, end=end, config=config, rebalance_dates=reb, pf=pf
        )

    # One book carried across all OOS folds (contiguous, non-overlapping) so it is
    # held continuously — only fold 0 establishes from cash. Without this, every
    # fold liquidated and rebuilt, inflating turnover ~8× with phantom costs.
    oos_pf = SimulatedPortfolio(
        starting_cash=float(bt.starting_cash),
        commission_bps=float(bt.commission_bps),
        spread_bps=float(bt.spread_bps),
        financing_bps=float(getattr(bt, "financing_bps", 0) or 0),  # P11 E2
    )

    def _sharpe(seg: SegmentResult) -> float:
        rets = (
            [(seg.equity[i] / seg.equity[i - 1]) - 1.0 for i in range(1, len(seg.equity))]
            if len(seg.equity) >= 2 else []
        )
        return sharpe_ratio(rets)

    fold_records: list[BacktestFold] = []
    for f_idx, fold in enumerate(folds):
        _save_progress(
            bt, 5 + int((f_idx + 1) / len(folds) * 90),
            f"fold {f_idx+1}/{len(folds)}: deterministic OOS",
        )
        is_seg = _seg(fold.is_start, fold.is_end)
        oos_seg = _seg(fold.oos_start, fold.oos_end, pf=oos_pf)
        oos_ret = (oos_seg.equity[-1] / oos_seg.equity[0] - 1.0) * 100 if oos_seg.equity else 0
        oos_dd = drawdown_pct(oos_seg.equity) * 100 if oos_seg.equity else 0
        rec = BacktestFold.objects.create(
            backtest=bt, fold_index=fold.index,
            is_start=fold.is_start, is_end=fold.is_end,
            oos_start=fold.oos_start, oos_end=fold.oos_end,
            winning_config=config,
            is_sharpe=Decimal(str(round(_sharpe(is_seg), 4))),
            oos_sharpe=Decimal(str(round(_sharpe(oos_seg), 4))),
            oos_return_pct=Decimal(str(round(oos_ret, 4))),
            oos_max_drawdown_pct=Decimal(str(round(oos_dd, 4))),
            candidates_scored=[],
        )
        _persist_segment_days(bt=bt, fold=rec, segment=BacktestDay.SEG_OOS, seg=oos_seg)
        fold_records.append(rec)

    _save_progress(bt, 96, "computing metrics")
    metrics = compute_stitched_metrics(bt, fold_records, None)
    BacktestMetrics.objects.update_or_create(backtest=bt, defaults=metrics)

    bt.status = Backtest.DONE
    bt.finished_at = timezone.now()
    _save_progress(bt, 100, "done")
    bt.save(update_fields=["status", "finished_at"])
