"""Celery task wrapper around the walk-forward orchestrator + orphan sweep."""
from __future__ import annotations

import datetime as dt
import logging

from celery import shared_task
from django.utils import timezone

from .models import Backtest

log = logging.getLogger(__name__)

ORPHAN_THRESHOLD_MIN = 15


@shared_task
def sweep_orphan_backtests() -> dict:
    """Mark abandoned backtests as failed.

    Mirror of `apps.runs.tasks.sweep_orphan_runs`: a backtest stuck in
    {queued, running} for > ORPHAN_THRESHOLD_MIN minutes with no active
    Celery task is treated as orphaned (worker restart / container
    recreation killed the subprocess before the task wrapper could
    record terminal status).
    """
    from hedgefund.celery import app as celery_app  # local: avoid load-time cycle

    cutoff = timezone.now() - dt.timedelta(minutes=ORPHAN_THRESHOLD_MIN)
    candidates = Backtest.objects.filter(
        status__in=Backtest.ACTIVE_STATUSES, created_at__lt=cutoff
    )
    if not candidates.exists():
        return {"swept": 0}

    active_ids: set[str] = set()
    try:
        inspect = celery_app.control.inspect(timeout=2.0)
        for _worker, tasks in (inspect.active() or {}).items():
            for t in tasks or []:
                tid = t.get("id")
                if tid:
                    active_ids.add(tid)
    except Exception:  # broker unreachable — better to do nothing than clobber live work
        log.warning("orphan-backtest sweep: could not inspect active tasks; aborting")
        return {"swept": 0, "error": "inspect_failed"}

    swept = 0
    for bt in candidates:
        if bt.celery_task_id and bt.celery_task_id in active_ids:
            continue  # still running, just slow
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


@shared_task
def run_backtest(backtest_id: int) -> None:
    bt = Backtest.objects.get(pk=backtest_id)
    try:
        from .walkforward import run_walkforward

        run_walkforward(bt)
    except Exception as exc:  # pragma: no cover
        log.exception("Backtest %s failed", backtest_id)
        current = Backtest.objects.filter(pk=backtest_id).values_list("status", flat=True).first()
        if current != Backtest.CANCELLED:
            bt.status = Backtest.FAILED
            bt.error_message = f"{type(exc).__name__}: {exc}"[:2000]
            bt.finished_at = timezone.now()
            bt.save(update_fields=["status", "error_message", "finished_at"])
        raise
