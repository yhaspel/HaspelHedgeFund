"""Order idempotency machinery.

A submit is wrapped so the idempotency state machine moves
    unsubmitted → submit_pending → acknowledged
A second submit on an order already in submit_pending or acknowledged is
refused locally. Network failures move the order to ``unknown`` and the
reconciliation engine adopts the broker side later (if it can find the
order by client_order_id), or marks it ``rejected`` after a grace window.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .interfaces import (
    Broker,
    BrokerError,
    BrokerTransientError,
    OrderSnapshot,
    OrderTicket,
)
from .models import BrokerOrder

UNKNOWN_GRACE = timedelta(minutes=10)


class IdempotencyConflict(Exception):
    """Raised when a duplicate submit is attempted locally."""


def submit_idempotent(
    *,
    order: BrokerOrder,
    broker: Broker,
) -> OrderSnapshot:
    """Submit `order` exactly once; safe under retry.

    The DB transition `unsubmitted → submit_pending` is done with a
    conditional UPDATE so two callers racing on the same order can only
    have one win — the loser sees `IdempotencyConflict`.
    """
    with transaction.atomic():
        updated = BrokerOrder.objects.filter(
            pk=order.pk,
            idempotency_state=BrokerOrder.IDEM_UNSUBMITTED,
        ).update(idempotency_state=BrokerOrder.IDEM_SUBMIT_PENDING)
        if not updated:
            # Already in flight or acknowledged.
            order.refresh_from_db()
            if order.idempotency_state in (
                BrokerOrder.IDEM_SUBMIT_PENDING,
                BrokerOrder.IDEM_ACKNOWLEDGED,
            ):
                raise IdempotencyConflict(
                    f"order {order.pk} is already {order.idempotency_state}"
                )
            raise IdempotencyConflict(
                f"order {order.pk} is in state {order.idempotency_state}; "
                "resolve before resubmitting"
            )
        order.refresh_from_db()

    ticket = OrderTicket(
        client_order_id=str(order.client_order_id),
        ticker=order.ticker,
        side=order.side,
        quantity=order.quantity,
        order_type=order.order_type,
        limit_price=order.limit_price,
        time_in_force=order.time_in_force,
    )
    try:
        snapshot = broker.submit_order(ticket)
    except BrokerTransientError as exc:
        # Don't know if the broker created the order. Move to unknown.
        with transaction.atomic():
            BrokerOrder.objects.filter(pk=order.pk).update(
                idempotency_state=BrokerOrder.IDEM_UNKNOWN,
                status=BrokerOrder.STATUS_ERROR,
                error_message=str(exc)[:500],
            )
        raise
    except BrokerError as exc:
        # Broker said no. Walk back to rejected so the user can decide.
        with transaction.atomic():
            BrokerOrder.objects.filter(pk=order.pk).update(
                idempotency_state=BrokerOrder.IDEM_UNSUBMITTED,
                status=BrokerOrder.STATUS_REJECTED,
                error_message=str(exc)[:500],
            )
        raise

    now = timezone.now()
    with transaction.atomic():
        BrokerOrder.objects.filter(pk=order.pk).update(
            idempotency_state=BrokerOrder.IDEM_ACKNOWLEDGED,
            status=_map_status(snapshot.status),
            broker_order_id=snapshot.broker_order_id or "",
            submitted_at=now,
            filled_quantity=Decimal(str(snapshot.filled_quantity)),
            avg_fill_price=snapshot.avg_fill_price,
            queued_until_open=bool(snapshot.queued_until_open),
            raw_broker_response=snapshot.raw or {},
        )
    order.refresh_from_db()
    return snapshot


def resolve_unknown(order: BrokerOrder, broker: Broker) -> str | None:
    """Recover an order stranded in `idempotency_state=unknown`.

    Returns the new status, or ``None`` if the broker has no record of the
    order yet and the grace window has not elapsed (caller should retry on
    the next reconcile pass).
    """
    if order.idempotency_state != BrokerOrder.IDEM_UNKNOWN:
        return None
    snapshot = broker.find_order_by_client_id(str(order.client_order_id))
    if snapshot is not None:
        with transaction.atomic():
            BrokerOrder.objects.filter(pk=order.pk).update(
                idempotency_state=BrokerOrder.IDEM_ACKNOWLEDGED,
                broker_order_id=snapshot.broker_order_id,
                status=_map_status(snapshot.status),
                filled_quantity=Decimal(str(snapshot.filled_quantity)),
                avg_fill_price=snapshot.avg_fill_price,
                raw_broker_response=snapshot.raw or {},
                error_message="",
            )
        return _map_status(snapshot.status)
    # No record on the broker side. Wait one grace window then fail closed.
    age = timezone.now() - order.created_at
    if age >= UNKNOWN_GRACE:
        with transaction.atomic():
            BrokerOrder.objects.filter(pk=order.pk).update(
                status=BrokerOrder.STATUS_REJECTED,
                error_message=(
                    "Broker has no record of this order after the grace "
                    "window. Marked rejected; create a new order if you "
                    "still want to trade."
                ),
            )
        return BrokerOrder.STATUS_REJECTED
    return None


def _map_status(snapshot_status: str) -> str:
    """Translate adapter snapshot statuses to BrokerOrder.STATUS_* values."""
    return {
        "draft": BrokerOrder.STATUS_DRAFT,
        "submitted": BrokerOrder.STATUS_SUBMITTED,
        "partial": BrokerOrder.STATUS_PARTIAL,
        "filled": BrokerOrder.STATUS_FILLED,
        "cancelled": BrokerOrder.STATUS_CANCELLED,
        "rejected": BrokerOrder.STATUS_REJECTED,
        "error": BrokerOrder.STATUS_ERROR,
    }.get(snapshot_status, BrokerOrder.STATUS_SUBMITTED)
