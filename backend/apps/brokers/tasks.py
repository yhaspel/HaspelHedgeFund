"""Celery tasks for the brokers app.

Three task families:
  - `poll_open_orders` runs every 30s (Celery beat schedule) and pulls
    fills for every active account's open orders.
  - `reconcile_account` runs every 5 minutes and after every submitted
    order (called by the view layer post-confirm).
  - `keep_ibkr_gateway_warm` runs every 2 minutes (P3a-2) — tickles the
    IBKR Client Portal Gateway and reconciles every IBKR account's
    `connection_status` against `/iserver/auth/status`. Recovers from
    `needs_reauth` once the user logs in to the gateway again.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db.models.functions import Coalesce
from django.utils import timezone

from hedgefund.offline import skip_when_offline

from .adapters.ibkr_gateway import IBKRGatewaySession
from .capabilities import AUTH_NONE, get_capabilities
from .demo_fills import evaluate_resting_demo_orders
from .interfaces import BrokerAuthError, BrokerError, BrokerTransientError
from .models import BrokerAccount, BrokerOrder, BrokerSyncEvent
from .reconcile import (
    credentials_confirmed_dead,
    poll_open_orders_for_account,
    pollable_orders_q,
    reconcile_account,
)

log = logging.getLogger(__name__)


@shared_task(name="apps.brokers.tasks.poll_open_orders")
def poll_open_orders() -> dict:
    """Walk every active account with at least one order still awaiting a venue
    answer and poll the adapter for new fills.

    "Awaiting an answer" is wider than `status in OPEN_STATUSES`: it also covers
    rows parked `idempotency_state=unknown` (a submit whose response was lost —
    Alpaca may hold the order) and bracket anchors whose protective legs are
    still live after the entry filled. See `reconcile.pollable_orders_q`."""
    if skip_when_offline("poll_open_orders"):
        return {"status": "skipped_offline"}
    summary = {"accounts_scanned": 0, "fills_written": 0, "needs_reauth": 0}
    account_ids = (
        BrokerOrder.objects
        .filter(pollable_orders_q())
        .values_list("broker_account_id", flat=True)
        # .order_by() clears BrokerOrder's Meta.ordering: without it the
        # ORDER BY created_at leaks into the SELECT DISTINCT list, so the
        # dedupe runs on (account_id, created_at) pairs and yields one row
        # *per open order* — re-polling the same account N times a cycle.
        .order_by()
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
        cap = get_capabilities(account.broker)
        is_demo = cap is not None and cap.auth_kind == AUTH_NONE
        try:
            if is_demo:
                # Demo books fill resting limit/stop orders against the
                # live price — they never hit an external adapter.
                summary["fills_written"] += evaluate_resting_demo_orders(account)
            else:
                summary["fills_written"] += poll_open_orders_for_account(account)
        except BrokerAuthError as exc:
            # A 401/403 can be a transient blip on a valid key, so confirm with
            # a fresh probe before darkening — only genuinely-dead credentials
            # flip out of ACTIVE so the next cycle skips the account instead of
            # re-failing every 30s. One concise warning replaces a traceback.
            if credentials_confirmed_dead(account):
                account.flag_needs_reauth()
                summary["needs_reauth"] += 1
                log.warning(
                    "poll_open_orders: account %s rejected (%s) — "
                    "flipped to needs_reauth", acc_id, exc,
                )
            else:
                log.warning(
                    "poll_open_orders: account %s transient auth error (%s) — "
                    "re-verified OK, left active", acc_id, exc,
                )
        except Exception:  # pragma: no cover - log and continue
            log.exception("poll_open_orders failed for account %s", acc_id)
    return summary


def sweep_stuck_confirmed_orders(grace_hours: int = 24) -> int:
    """Terminate orders stuck in `confirmed` after a failed submission.

    A submission that errors without reaching the venue (no `broker_order_id`)
    is walked back to `confirmed` with the error recorded. `confirmed` is not in
    `OPEN_STATUSES`, so `poll_open_orders` never revisits it — a genuinely-dead
    order (e.g. an order-domain 403 that predates the adapter's reject mapping,
    or an auth failure whose credentials never recovered) would linger forever,
    obscuring the book. Reap such rows to `rejected` once they are older than
    `grace_hours` (the window in which a transient auth blip could still
    recover). Returns the number reaped.

    The grace window is measured from the SUBMIT ATTEMPT (`submit_attempted_at`,
    falling back to `created_at` for rows that never reached the broker call).
    Measuring from `created_at` reaped held orders on their very first release:
    a `pending_open` order drafted at Friday's close is two days old by
    Tuesday's open but zero seconds into its submission."""
    cutoff = timezone.now() - timedelta(hours=grace_hours)
    stuck = BrokerOrder.objects.filter(
        status=BrokerOrder.STATUS_CONFIRMED,
        broker_order_id="",
    ).annotate(
        attempted_at=Coalesce("submit_attempted_at", "created_at"),
    ).filter(attempted_at__lt=cutoff).exclude(error_message="")
    reaped = 0
    for order in stuck:
        order.status = BrokerOrder.STATUS_REJECTED
        order.error_message = (order.error_message or "")[:450] + " [swept: stuck confirmed]"
        order.save(update_fields=["status", "error_message"])
        reaped += 1
    if reaped:
        log.warning("sweep_stuck_confirmed_orders: reaped %d stuck order(s)", reaped)
    return reaped


@shared_task(name="apps.brokers.tasks.reconcile_all_accounts")
def reconcile_all_accounts() -> dict:
    """Periodic full reconciliation across every active account."""
    if skip_when_offline("reconcile_all_accounts"):
        return {"status": "skipped_offline"}
    summary = {"accounts": 0, "drift_events": 0, "stuck_orders_reaped": 0}
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
    try:
        summary["stuck_orders_reaped"] = sweep_stuck_confirmed_orders()
    except Exception:  # pragma: no cover
        log.exception("sweep_stuck_confirmed_orders failed")
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


# P3a-2: keep the IBKR Client Portal Gateway session warm. ADR 0011 §4.
#
# There is exactly one gateway sidecar per deployment, so the
# tickle+auth-status pair captures the state of every IBKR account
# simultaneously. The task:
#   - probes the gateway once (tickle keeps the session alive; auth-status
#     is the authoritative health signal)
#   - flips every monitored IBKR account to the matching status:
#       authenticated + connected → STATUS_ACTIVE (recovers from needs_reauth
#                                   once the user logs back in)
#       otherwise / unreachable   → STATUS_NEEDS_REAUTH
#   - touches only IBKR accounts in ACTIVE or NEEDS_REAUTH state. Accounts
#     mid-wizard (STATUS_CONNECTING) and explicitly disconnected ones
#     (STATUS_DISABLED) are left alone.


@shared_task(name="apps.brokers.tasks.keep_ibkr_gateway_warm")
def keep_ibkr_gateway_warm() -> dict:
    if skip_when_offline("keep_ibkr_gateway_warm"):
        return {"status": "skipped_offline"}
    summary: dict = {
        "monitored_accounts": 0,
        "checked": False,
        "authenticated": False,
        "now_active": 0,
        "now_needs_reauth": 0,
        "transitions": 0,
        "error": None,
    }

    monitored = list(
        BrokerAccount.objects.filter(
            broker="ibkr",
            is_active=True,
            connection_status__in=(
                BrokerAccount.STATUS_ACTIVE,
                BrokerAccount.STATUS_NEEDS_REAUTH,
            ),
        ),
    )
    summary["monitored_accounts"] = len(monitored)
    if not monitored:
        return summary

    authenticated = False
    try:
        with IBKRGatewaySession() as session:
            try:
                session.tickle()
            except (BrokerError, BrokerTransientError):
                # Tickle alone isn't conclusive — auth-status is the
                # authoritative signal. Swallow and fall through.
                pass
            try:
                status_payload = session.auth_status()
            except (BrokerError, BrokerTransientError) as exc:
                summary["error"] = f"auth_status failed: {exc}"
                status_payload = {}
            authenticated = bool(status_payload.get("authenticated")) and bool(
                status_payload.get("connected"),
            )
            summary["checked"] = True
    except Exception as exc:  # pragma: no cover - safety net
        log.exception("keep_ibkr_gateway_warm: unexpected error")
        summary["error"] = f"unexpected: {exc}"
        # Treat as transient: flip everything to needs_reauth so submissions
        # bail out at the gate until the next tick confirms the state.
        authenticated = False

    summary["authenticated"] = authenticated
    target_status = (
        BrokerAccount.STATUS_ACTIVE if authenticated
        else BrokerAccount.STATUS_NEEDS_REAUTH
    )

    # Batch-update only the rows whose status would change. UPDATEs are
    # cheap but a single WHERE with a NOT-equal predicate is cheaper than
    # touching every row.
    pks_to_update = [a.pk for a in monitored if a.connection_status != target_status]
    if pks_to_update:
        BrokerAccount.objects.filter(pk__in=pks_to_update).update(
            connection_status=target_status,
        )
        summary["transitions"] = len(pks_to_update)

    if target_status == BrokerAccount.STATUS_ACTIVE:
        summary["now_active"] = len(monitored)
    else:
        summary["now_needs_reauth"] = len(monitored)
    return summary
