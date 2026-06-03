"""Bracket / OTO / OCO group assembly + status derivation (P3a, ADR 0015).

Alpaca's native unit is *one parent order with nested ``legs[]``*. We mirror
that with a self-referential ``parent_order`` FK on ``BrokerOrder``: the
protective legs point at their **anchor** — the ``entry`` leg for a
bracket/OTO, or the ``take_profit`` leg for a standalone OCO on a held
position. Every row in a set shares one ``group_id`` UUID so the pending
page and reconciliation can fetch the whole set in one query.

This module owns three things and adds no broker call and no new model:

  - the leg-side rule (exits are the opposite side of the entry),
  - ``create_group`` — build the anchor + protective leg rows in one txn,
  - ``derive_group_status`` — a *pure function of the leg rows*, recomputed
    on every poll and never stored (storing it would invite drift).
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from django.db import transaction

from .models import BrokerOrder

# --- Group-level status labels ----------------------------------------------
#
# These are *derived* group states, distinct from the per-row
# BrokerOrder.STATUS_* values. A group is `working` while live, `closed` once
# it has no remaining live order (an exit filled, or the user tore the exits
# down after the entry filled), `cancelled` only if withdrawn before the entry
# filled, and `error` on an unexpected terminal or a failed protective leg.

GROUP_DRAFT = "draft"
GROUP_CONFIRMED = "confirmed"
GROUP_WORKING = "working"
GROUP_CLOSED = "closed"
GROUP_CANCELLED = "cancelled"
GROUP_ERROR = "error"


def exit_side(entry_side: str) -> str:
    """Protective exits are the opposite side of the entry: a long bracket
    buys to enter and sells to protect; a short bracket is the mirror."""
    return "sell" if entry_side == "buy" else "buy"


def is_group_anchor(order: BrokerOrder) -> bool:
    """An anchor is the row a confirmation / idempotency key / reconcile
    fetch hangs on: a bracket/OTO entry, or a standalone OCO's take-profit
    primary. It has no parent and carries a group_id."""
    return (
        order.group_id is not None
        and order.parent_order_id is None
        and order.leg_role in (BrokerOrder.LEG_ENTRY, BrokerOrder.LEG_TAKE_PROFIT)
    )


def is_child_leg(order: BrokerOrder) -> bool:
    """A protective leg submitted and polled only through its anchor."""
    return order.parent_order_id is not None


# --- Group assembly ---------------------------------------------------------


@transaction.atomic
def create_group(
    *,
    account,
    decision,
    ticker: str,
    side: str,
    quantity: Decimal,
    time_in_force: str,
    order_class: str,
    entry_order_type: str | None = None,
    entry_limit_price: Decimal | None = None,
    take_profit_limit_price: Decimal | None = None,
    stop_loss_stop_price: Decimal | None = None,
    stop_loss_limit_price: Decimal | None = None,
) -> BrokerOrder:
    """Create the anchor + protective leg rows under one shared group_id and
    return the **anchor**.

    For ``bracket`` / ``oto`` the request ``side`` is the **entry** side and
    the exits are the opposite side. For ``oco`` (a held position, no entry)
    the request ``side`` IS the exit side and the take-profit leg is the
    anchor. Validation (which prices are required for which order_class) is
    the caller's job; this only persists what it is handed.
    """
    group_id = uuid.uuid4()
    common = dict(
        broker_account=account,
        decision=decision,
        ticker=ticker,
        quantity=quantity,
        time_in_force=time_in_force,
        group_id=group_id,
    )

    if order_class in ("bracket", "oto"):
        entry = BrokerOrder.objects.create(
            side=side,
            order_type=entry_order_type or BrokerOrder.TYPE_MARKET,
            limit_price=entry_limit_price,
            leg_role=BrokerOrder.LEG_ENTRY,
            **common,
        )
        anchor = entry
        legs_parent = entry
        exits_side = exit_side(side)
    elif order_class == "oco":
        entry = None
        anchor = None              # set to the take-profit leg below
        legs_parent = None
        exits_side = side
    else:  # pragma: no cover - guarded by the view
        raise ValueError(f"unknown grouped order_class {order_class!r}")

    take_profit = None
    if take_profit_limit_price is not None:
        take_profit = BrokerOrder.objects.create(
            side=exits_side,
            order_type=BrokerOrder.TYPE_LIMIT,
            limit_price=take_profit_limit_price,
            leg_role=BrokerOrder.LEG_TAKE_PROFIT,
            parent_order=legs_parent,
            **common,
        )

    if order_class == "oco":
        # No entry — the take-profit is the primary; the stop-loss FKs to it.
        anchor = take_profit
        legs_parent = take_profit
        if take_profit is not None:
            BrokerOrder.objects.filter(pk=take_profit.pk).update(parent_order=None)

    if stop_loss_stop_price is not None:
        sl_type = (
            BrokerOrder.TYPE_STOP_LIMIT
            if stop_loss_limit_price is not None
            else BrokerOrder.TYPE_STOP
        )
        BrokerOrder.objects.create(
            side=exits_side,
            order_type=sl_type,
            stop_price=stop_loss_stop_price,
            limit_price=stop_loss_limit_price,
            leg_role=BrokerOrder.LEG_STOP_LOSS,
            parent_order=legs_parent,
            **common,
        )

    if anchor is None:  # pragma: no cover - guarded by the view
        raise ValueError("group has no anchor leg")
    anchor.refresh_from_db()
    return anchor


# --- Status derivation ------------------------------------------------------
#
# Per-leg status normalization — the eight BrokerOrder.STATUS_CHOICES collapse
# to five buckets (the resolution rules below reference the raw statuses
# directly where the distinction matters, e.g. rejected vs error).

PENDING = "PENDING"      # draft, confirmed — not yet at the broker
WORKING = "WORKING"      # submitted, partial — live at the broker
FILLED = "FILLED"        # filled — terminally filled
CANCELLED = "CANCELLED"  # cancelled — withdrawn, no remaining qty
BROKEN = "BROKEN"        # error, rejected — unexpected terminal

_BUCKET = {
    BrokerOrder.STATUS_DRAFT: PENDING,
    BrokerOrder.STATUS_CONFIRMED: PENDING,
    BrokerOrder.STATUS_SUBMITTED: WORKING,
    BrokerOrder.STATUS_PARTIAL: WORKING,
    BrokerOrder.STATUS_FILLED: FILLED,
    BrokerOrder.STATUS_CANCELLED: CANCELLED,
    BrokerOrder.STATUS_ERROR: BROKEN,
    BrokerOrder.STATUS_REJECTED: BROKEN,
}


def bucket(status: str) -> str:
    return _BUCKET.get(status, BROKEN)


def derive_group_status(anchor: BrokerOrder) -> str:
    """Derive the group status from the anchor + its child legs.

    First match wins — the rule order is load-bearing. ``closed`` means "no
    remaining live order" (an exit filled, or the user cancelled the exits
    after the entry filled); ``cancelled`` is reserved for a set withdrawn
    *before* the entry filled; a rejected protective leg on a live position is
    a safety event (``error``). See ADR 0015 and the plan's status table.
    """
    children = list(anchor.child_legs.all())
    is_oco = anchor.leg_role == BrokerOrder.LEG_TAKE_PROFIT

    if is_oco:
        entry = None
        exits = [anchor, *children]
    else:
        entry = anchor                 # leg_role == entry (bracket / OTO)
        exits = children

    all_legs = [anchor, *children]
    anchor_b = bucket(anchor.status)
    entry_b = bucket(entry.status) if entry is not None else None
    any_exit_filled = any(e.status == BrokerOrder.STATUS_FILLED for e in exits)

    # 1. anchor still pending and nothing has reached the broker.
    if anchor_b == PENDING and all(bucket(leg.status) == PENDING for leg in all_legs):
        return (
            GROUP_DRAFT
            if anchor.status == BrokerOrder.STATUS_DRAFT
            else GROUP_CONFIRMED
        )

    # 2. any leg in an unexpected `error` state.
    if any(leg.status == BrokerOrder.STATUS_ERROR for leg in all_legs):
        return GROUP_ERROR

    # 3. a protective leg rejected while the protected position is live.
    position_live = (entry is not None and entry_b == FILLED) or is_oco
    if position_live and any(
        e.status == BrokerOrder.STATUS_REJECTED for e in exits
    ):
        return GROUP_ERROR

    # 4. an exit filled — position flat (sibling cancel is expected, not error).
    if any_exit_filled:
        return GROUP_CLOSED

    # 5. entry cancelled/rejected before any exit filled — nothing opened.
    if entry is not None and entry.status in (
        BrokerOrder.STATUS_CANCELLED, BrokerOrder.STATUS_REJECTED,
    ):
        return GROUP_CANCELLED

    # 6. whole set withdrawn before any fill (covers the entry-less OCO case).
    if all(bucket(leg.status) == CANCELLED for leg in all_legs):
        return GROUP_CANCELLED

    # 7. entry filled and all exits cancelled — user tore the protection down.
    if (
        entry is not None
        and entry_b == FILLED
        and exits
        and all(bucket(e.status) == CANCELLED for e in exits)
    ):
        return GROUP_CLOSED

    # 8. otherwise the group is live (covers held exits + partial fills).
    return GROUP_WORKING
