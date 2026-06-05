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

from django.db import models, transaction
from django.utils import timezone

from .interfaces import (
    Broker,
    BrokerAuthError,
    BrokerError,
    BrokerTransientError,
    OrderMeta,
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
        # Carry the trigger / trail so a standalone stop / stop_limit /
        # trailing_stop actually reaches the adapter (closes the latent
        # "stop becomes market" path at the framework layer too).
        stop_price=order.stop_price,
        trail_price=order.trail_price,
        trail_percent=order.trail_percent,
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
    except BrokerAuthError as exc:
        # Credentials were rejected (401/403) — the order never reached the
        # venue. Walk it back to a clean, re-submittable state (NOT rejected,
        # which would imply the venue refused the order) and flag the account
        # so nothing resubmits until it is re-authenticated.
        with transaction.atomic():
            BrokerOrder.objects.filter(pk=order.pk).update(
                idempotency_state=BrokerOrder.IDEM_UNSUBMITTED,
                status=BrokerOrder.STATUS_CONFIRMED,
                error_message=str(exc)[:500],
            )
            order.broker_account.flag_needs_reauth()
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


# --- Grouped (bracket / OTO / OCO) submit -----------------------------------


def _carrier_ticket(anchor: BrokerOrder) -> tuple[OrderTicket, str]:
    """Build the anchor's carrier OrderTicket from the persisted leg rows and
    return (ticket, order_class). The anchor is the bracket/OTO entry, or the
    standalone OCO's take-profit primary."""
    children = list(anchor.child_legs.all())
    tp_leg = next(
        (c for c in children if c.leg_role == BrokerOrder.LEG_TAKE_PROFIT), None,
    )
    sl_leg = next(
        (c for c in children if c.leg_role == BrokerOrder.LEG_STOP_LOSS), None,
    )

    if anchor.leg_role == BrokerOrder.LEG_TAKE_PROFIT:
        # Standalone OCO: the anchor carries the take-profit limit itself.
        order_class = "oco"
        take_profit_limit_price = anchor.limit_price
    else:
        # Bracket (both exits) or OTO (exactly one).
        has_tp = tp_leg is not None
        has_sl = sl_leg is not None
        order_class = "bracket" if (has_tp and has_sl) else "oto"
        take_profit_limit_price = tp_leg.limit_price if tp_leg is not None else None

    stop_loss_stop_price = sl_leg.stop_price if sl_leg is not None else None
    stop_loss_limit_price = (
        sl_leg.limit_price
        if (sl_leg is not None and sl_leg.order_type == BrokerOrder.TYPE_STOP_LIMIT)
        else None
    )
    ticket = OrderTicket(
        client_order_id=str(anchor.client_order_id),
        ticker=anchor.ticker,
        side=anchor.side,
        quantity=anchor.quantity,
        order_type=anchor.order_type,
        limit_price=anchor.limit_price,
        time_in_force=anchor.time_in_force,
        order_class=order_class,
        take_profit_limit_price=take_profit_limit_price,
        stop_loss_stop_price=stop_loss_stop_price,
        stop_loss_limit_price=stop_loss_limit_price,
    )
    return ticket, order_class


def _match_leg_to_child(
    leg: OrderSnapshot, children: list[BrokerOrder],
) -> BrokerOrder | None:
    """Map a returned Alpaca leg onto a local child row by the leg's order
    type — `limit` → take-profit row, `stop` / `stop_limit` → stop-loss row.
    Unambiguous for every supported shape (a long bracket's two same-side
    exits differ precisely by type). See ADR 0015."""
    if leg.order_type == "limit":
        role = BrokerOrder.LEG_TAKE_PROFIT
    else:
        role = BrokerOrder.LEG_STOP_LOSS
    return next((c for c in children if c.leg_role == role), None)


def _backfill_legs(anchor: BrokerOrder, snapshot: OrderSnapshot) -> None:
    """Backfill each child leg from the parent snapshot's `.legs`: broker id,
    status, and acknowledged idempotency state. Idempotent — safe to re-run on
    every reconcile (it just refreshes the mirror)."""
    children = list(anchor.child_legs.all())
    for leg in snapshot.legs:
        child = _match_leg_to_child(leg, children)
        if child is None:
            continue
        BrokerOrder.objects.filter(pk=child.pk).update(
            idempotency_state=BrokerOrder.IDEM_ACKNOWLEDGED,
            status=_map_status(leg.status),
            broker_order_id=leg.broker_order_id or child.broker_order_id or "",
            filled_quantity=Decimal(str(leg.filled_quantity)),
            avg_fill_price=leg.avg_fill_price,
            raw_broker_response=leg.raw or {},
        )


def submit_bracket_idempotent(
    *,
    anchor: BrokerOrder,
    broker: Broker,
) -> OrderSnapshot:
    """Submit a bracket / OTO / OCO group exactly once, keyed on the anchor's
    native `client_order_id`. Runs the idempotency state machine on the anchor
    only; the child legs are local mirror rows backfilled from the returned
    `.legs`. Safe under retry — Alpaca's 422 + the local state machine prevent
    a second group."""
    with transaction.atomic():
        updated = BrokerOrder.objects.filter(
            pk=anchor.pk,
            idempotency_state=BrokerOrder.IDEM_UNSUBMITTED,
        ).update(idempotency_state=BrokerOrder.IDEM_SUBMIT_PENDING)
        if not updated:
            anchor.refresh_from_db()
            if anchor.idempotency_state in (
                BrokerOrder.IDEM_SUBMIT_PENDING,
                BrokerOrder.IDEM_ACKNOWLEDGED,
            ):
                raise IdempotencyConflict(
                    f"order {anchor.pk} is already {anchor.idempotency_state}",
                )
            raise IdempotencyConflict(
                f"order {anchor.pk} is in state {anchor.idempotency_state}; "
                "resolve before resubmitting",
            )
        anchor.refresh_from_db()

    ticket, order_class = _carrier_ticket(anchor)
    try:
        if order_class == "oco":
            snapshot = broker.submit_protective(ticket)
        else:
            snapshot = broker.submit_bracket(ticket)
    except BrokerTransientError as exc:
        with transaction.atomic():
            BrokerOrder.objects.filter(pk=anchor.pk).update(
                idempotency_state=BrokerOrder.IDEM_UNKNOWN,
                status=BrokerOrder.STATUS_ERROR,
                error_message=str(exc)[:500],
            )
        raise
    except BrokerAuthError as exc:
        # Credentials rejected (401/403) — the group never reached the venue.
        # Walk the whole group back to a clean, re-submittable state (NOT
        # rejected) and flag the account so nothing resubmits until re-auth.
        with transaction.atomic():
            BrokerOrder.objects.filter(
                models.Q(pk=anchor.pk) | models.Q(parent_order=anchor),
            ).update(
                idempotency_state=BrokerOrder.IDEM_UNSUBMITTED,
                status=BrokerOrder.STATUS_CONFIRMED,
                error_message=str(exc)[:500],
            )
            anchor.broker_account.flag_needs_reauth()
        raise
    except BrokerError as exc:
        # Broker said no. Walk the whole group back so the user can decide.
        with transaction.atomic():
            BrokerOrder.objects.filter(
                models.Q(pk=anchor.pk) | models.Q(parent_order=anchor),
            ).update(
                idempotency_state=BrokerOrder.IDEM_UNSUBMITTED,
                status=BrokerOrder.STATUS_REJECTED,
                error_message=str(exc)[:500],
            )
        raise

    now = timezone.now()
    with transaction.atomic():
        BrokerOrder.objects.filter(pk=anchor.pk).update(
            idempotency_state=BrokerOrder.IDEM_ACKNOWLEDGED,
            status=_map_status(snapshot.status),
            broker_order_id=snapshot.broker_order_id or "",
            submitted_at=now,
            filled_quantity=Decimal(str(snapshot.filled_quantity)),
            avg_fill_price=snapshot.avg_fill_price,
            queued_until_open=bool(snapshot.queued_until_open),
            raw_broker_response=snapshot.raw or {},
        )
        _backfill_legs(anchor, snapshot)
    anchor.refresh_from_db()
    return snapshot


def resolve_unknown(order: BrokerOrder, broker: Broker) -> str | None:
    """Recover an order stranded in `idempotency_state=unknown`.

    Returns the new status, or ``None`` if the broker has no record of the
    order yet and the grace window has not elapsed (caller should retry on
    the next reconcile pass).
    """
    if order.idempotency_state != BrokerOrder.IDEM_UNKNOWN:
        return None
    snapshot = broker.find_order_by_client_id(
        str(order.client_order_id),
        order_meta=OrderMeta(
            ticker=order.ticker,
            side=order.side,
            quantity=order.quantity,
            created_at=order.created_at,
        ),
    )
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
            # A group anchor parked `unknown` is adopted with its legs:
            # find_order_by_client_id returns the parent WITH legs[], which we
            # re-map onto the local child rows (ADR 0008 contract, reused).
            if snapshot.legs:
                _backfill_legs(order, snapshot)
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
