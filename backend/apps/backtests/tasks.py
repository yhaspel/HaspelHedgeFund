"""Celery task wrapper around the walk-forward orchestrator."""
from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from .models import Backtest

log = logging.getLogger(__name__)


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
