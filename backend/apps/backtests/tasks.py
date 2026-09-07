"""Celery task wrapper around the walk-forward orchestrator + orphan sweep."""
from __future__ import annotations

import datetime as dt
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import Backtest

log = logging.getLogger(__name__)

ORPHAN_THRESHOLD_MIN = 15

# A council walk-forward over a 20-name × 3-year master window is a multi-hour
# job, but without a limit a wedged provider connection pins a prefork slot
# forever. soft_time_limit raises SoftTimeLimitExceeded inside the task (so the
# except branch records a real terminal status); time_limit is the hard SIGKILL
# backstop 30 minutes later, after which the orphan sweeper cleans up.
RUN_BACKTEST_SOFT_TIME_LIMIT = int(
    getattr(settings, "BACKTEST_SOFT_TIME_LIMIT_SECONDS", 6 * 60 * 60)
)
RUN_BACKTEST_TIME_LIMIT = int(
    getattr(settings, "BACKTEST_TIME_LIMIT_SECONDS", int(6.5 * 60 * 60))
)


@shared_task
def sweep_orphan_backtests() -> dict:
    """Mark abandoned backtests as failed.

    Mirror of `apps.runs.tasks.sweep_orphan_runs`: a backtest stuck in
    {queued, running} for > ORPHAN_THRESHOLD_MIN minutes with no active
    Celery task is treated as orphaned (worker restart / container
    recreation killed the subprocess before the task wrapper could
    record terminal status).

    A task is "alive" if the worker reports it in **active**, **reserved** or
    **scheduled**, or if the row wrote a heartbeat within the threshold. The
    reserved/scheduled queues matter: with prefork prefetch a task that is merely
    queued behind long-running work sits in ``reserved()``, never ``active()``,
    so consulting only ``active()`` failed perfectly healthy backtests that then
    executed anyway once a slot freed up.
    """
    from hedgefund.celery import app as celery_app  # local: avoid load-time cycle

    cutoff = timezone.now() - dt.timedelta(minutes=ORPHAN_THRESHOLD_MIN)
    candidates = Backtest.objects.filter(
        status__in=Backtest.ACTIVE_STATUSES, created_at__lt=cutoff
    )
    if not candidates.exists():
        return {"swept": 0}

    live_ids: set[str] = set()
    try:
        inspect = celery_app.control.inspect(timeout=2.0)
        for probe in (inspect.active, inspect.reserved, inspect.scheduled):
            reply = probe() or {}
            if not isinstance(reply, dict):
                continue
            for _worker, tasks in reply.items():
                for t in tasks or []:
                    # scheduled() wraps the task under a "request" key.
                    payload = t.get("request") if isinstance(t, dict) else None
                    tid = (payload or t or {}).get("id")
                    if tid:
                        live_ids.add(tid)
    except Exception:  # broker unreachable — better to do nothing than clobber live work
        log.warning("orphan-backtest sweep: could not inspect active tasks; aborting")
        return {"swept": 0, "error": "inspect_failed"}

    swept = 0
    for bt in candidates:
        if bt.celery_task_id and bt.celery_task_id in live_ids:
            continue  # active, prefetched or scheduled — just slow
        if bt.heartbeat_at and bt.heartbeat_at >= cutoff:
            continue  # the worker wrote progress inside the window: alive
        bt.status = Backtest.FAILED
        bt.error_message = (
            "Orphaned: no active Celery task for this backtest; worker likely "
            "restarted mid-execution."
        )
        bt.finished_at = timezone.now()
        bt.save(update_fields=["status", "error_message", "finished_at"])
        swept += 1
    if swept:
        log.warning("orphan-backtest sweep marked %d backtests failed", swept)
    return {"swept": swept}


@shared_task(
    soft_time_limit=RUN_BACKTEST_SOFT_TIME_LIMIT,
    time_limit=RUN_BACKTEST_TIME_LIMIT,
)
def run_backtest(backtest_id: int) -> None:
    from .walkforward import BacktestNotClaimable, run_walkforward

    bt = Backtest.objects.get(pk=backtest_id)
    try:
        run_walkforward(bt)
    except BacktestNotClaimable:
        # The row was already terminal (user cancel, or the sweeper failed it)
        # when this task got a worker slot. run_walkforward refused to touch it;
        # do NOT rewrite the terminal status here either.
        log.info("run_backtest: backtest %s was not claimable; leaving as-is", backtest_id)
        return
    except Exception as exc:  # pragma: no cover
        from .exceptions import BudgetExceeded, SparseCache

        log.exception("Backtest %s failed", backtest_id)
        current = Backtest.objects.filter(pk=backtest_id).values_list("status", flat=True).first()
        if current != Backtest.CANCELLED:
            if isinstance(exc, BudgetExceeded):
                bt.status = Backtest.ABORTED_BUDGET
            elif isinstance(exc, SparseCache):
                bt.status = Backtest.ABORTED_PARTIAL
            else:
                bt.status = Backtest.FAILED
            bt.error_message = f"{type(exc).__name__}: {exc}"[:2000]
            bt.finished_at = timezone.now()
            bt.save(update_fields=["status", "error_message", "finished_at"])
        raise
