"""Celery tasks for the brokers app.

Two task families:
  - `poll_open_orders` runs every 30s (Celery beat schedule) and pulls
    fills for every active account's open orders.
  - `reconcile_account` runs every 5 minutes and after every submitted
    order (called by the view layer post-confirm).
"""
from __future__ import annotations

import logging

from celery import shared_task

from .models import BrokerAccount, BrokerOrder, BrokerSyncEvent
from .reconcile import poll_open_orders_for_account, reconcile_account

log = logging.getLogger(__name__)


@shared_task(name="apps.brokers.tasks.poll_open_orders")
def poll_open_orders() -> dict:
    """Walk every active account with at least one open order and poll the
    adapter for new fills."""
    summary = {"accounts_scanned": 0, "fills_written": 0}
    account_ids = (
        BrokerOrder.objects
        .filter(status__in=BrokerOrder.OPEN_STATUSES)
        .values_list("broker_account_id", flat=True)
        .distinct()
    )
    for acc_id in account_ids:
        account = BrokerAccount.objects.filter(
            pk=acc_id, is_active=True,
            connection_status=BrokerAccount.STATUS_ACTIVE,
        ).first()
        if account is None:
            continue
        summary["accounts_scanned"] += 1
        try:
            summary["fills_written"] += poll_open_orders_for_account(account)
        except Exception:  # pragma: no cover - log and continue
            log.exception("poll_open_orders failed for account %s", acc_id)
    return summary


@shared_task(name="apps.brokers.tasks.reconcile_all_accounts")
def reconcile_all_accounts() -> dict:
    """Periodic full reconciliation across every active account."""
    summary = {"accounts": 0, "drift_events": 0}
    for account in BrokerAccount.objects.filter(
        is_active=True,
        connection_status=BrokerAccount.STATUS_ACTIVE,
    ):
        summary["accounts"] += 1
        try:
            event = reconcile_account(
                account, triggered_by=BrokerSyncEvent.TRIGGER_PERIODIC,
            )
            if event.drift_detected:
                summary["drift_events"] += 1
        except Exception:  # pragma: no cover
            log.exception("reconcile_account failed for account %s", account.pk)
    return summary


@shared_task(name="apps.brokers.tasks.reconcile_account_task")
def reconcile_account_task(
    account_id: int,
    *,
    triggered_by: str = BrokerSyncEvent.TRIGGER_POST_ORDER,
) -> int:
    account = BrokerAccount.objects.filter(pk=account_id).first()
    if account is None:
        return 0
    event = reconcile_account(account, triggered_by=triggered_by)
    return event.id
