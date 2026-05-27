"""Reconciliation engine + the broken-leg group hook.

Two distinct entry points:

  - `poll_open_orders_for_account`: every 30s by Celery beat. Walks each
    open BrokerOrder, calls `broker.get_order`, ingests new fills, writes
    a LedgerEntry per fill, advances status.

  - `reconcile_account`: every 5min and once after every submitted order.
    Compares broker positions/cash to the broker Portfolio and squares
    residual drift by writing reconciliation_adjustment LedgerEntry rows
    so cash_balance + Σ cash_delta stays consistent.

Both write a BrokerSyncEvent row for auditability.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from apps.portfolios.models import LedgerEntry, Portfolio, Position

from .capabilities import AUTH_NONE, get_adapter_factory, get_capabilities
from .idempotency import resolve_unknown
from .interfaces import Broker, BrokerError, FillSnapshot, PositionSnapshot
from .models import BrokerAccount, BrokerFill, BrokerOrder, BrokerSyncEvent


def _money(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _qty(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def get_broker(account: BrokerAccount) -> Broker:
    factory = get_adapter_factory(account.broker)
    if factory is None:
        raise RuntimeError(f"no adapter registered for broker {account.broker!r}")
    return factory(account)


# --- poll_open_orders -------------------------------------------------------


def ingest_order_fills(order: BrokerOrder, broker: Broker | None = None) -> int:
    """Pull `order`'s fills from the broker and write any new `BrokerFill` +
    `LedgerEntry(broker_fill)` rows. Returns # of fills written. Idempotent."""
    adapter = broker or get_broker(order.broker_account)
    if order.idempotency_state == BrokerOrder.IDEM_UNKNOWN:
        resolve_unknown(order, adapter)
        order.refresh_from_db()
    if not order.broker_order_id:
        return 0
    try:
        snapshot = adapter.get_order(order.broker_order_id)
    except BrokerError as exc:
        order.error_message = str(exc)[:500]
        order.save(update_fields=["error_message"])
        return 0
    fills = adapter.get_recent_fills(order.created_at)
    written = 0
    for fill in fills:
        if fill.broker_order_id != order.broker_order_id:
            continue
        if _ingest_fill(order, fill):
            written += 1
    _apply_snapshot_status(
        order, snapshot.status, snapshot.filled_quantity, snapshot.avg_fill_price,
    )
    return written


def poll_open_orders_for_account(account: BrokerAccount) -> int:
    """Pull every open order and ingest any new fills. Returns # of fills written."""
    broker = get_broker(account)
    open_orders = BrokerOrder.objects.filter(
        broker_account=account, status__in=BrokerOrder.OPEN_STATUSES,
    )
    fills_written = 0
    for order in open_orders:
        # If the order was stuck in `unknown` (network glitch on submit),
        # try to adopt it on the broker side first.
        if order.idempotency_state == BrokerOrder.IDEM_UNKNOWN:
            resolve_unknown(order, broker)
            order.refresh_from_db()
            if not order.broker_order_id:
                continue
        try:
            snapshot = broker.get_order(order.broker_order_id)
        except BrokerError as exc:
            order.error_message = str(exc)[:500]
            order.save(update_fields=["error_message"])
            continue

        fills = broker.get_recent_fills(order.created_at)
        order_fills = [f for f in fills if f.broker_order_id == order.broker_order_id]
        for fill in order_fills:
            written = _ingest_fill(order, fill)
            if written:
                fills_written += 1

        _apply_snapshot_status(order, snapshot.status, snapshot.filled_quantity,
                                snapshot.avg_fill_price)
    return fills_written


def _ingest_fill(order: BrokerOrder, fill: FillSnapshot) -> bool:
    """Idempotent fill ingest. Returns True if a new row was created."""
    obj, created = BrokerFill.objects.get_or_create(
        order=order, broker_fill_id=fill.broker_fill_id,
        defaults={
            "quantity": _qty(fill.quantity),
            "price": Decimal(str(fill.price)).quantize(Decimal("0.0001")),
            "filled_at": fill.filled_at,
            "raw_broker_response": fill.raw or {},
        },
    )
    if not created:
        return False

    # Write a ledger entry + apply to the broker Portfolio.
    _apply_fill_to_portfolio(order, obj)
    return True


def _apply_snapshot_status(
    order: BrokerOrder,
    status: str,
    filled_qty,
    avg_fill_price,
) -> None:
    new_status = {
        "submitted": BrokerOrder.STATUS_SUBMITTED,
        "partial": BrokerOrder.STATUS_PARTIAL,
        "filled": BrokerOrder.STATUS_FILLED,
        "cancelled": BrokerOrder.STATUS_CANCELLED,
        "rejected": BrokerOrder.STATUS_REJECTED,
        "error": BrokerOrder.STATUS_ERROR,
    }.get(status, order.status)
    updates = {
        "status": new_status,
        "filled_quantity": Decimal(str(filled_qty or 0)),
    }
    if avg_fill_price is not None:
        updates["avg_fill_price"] = Decimal(str(avg_fill_price))
    if new_status == BrokerOrder.STATUS_FILLED and order.filled_at is None:
        updates["filled_at"] = timezone.now()
    if new_status == BrokerOrder.STATUS_CANCELLED and order.cancelled_at is None:
        updates["cancelled_at"] = timezone.now()
    BrokerOrder.objects.filter(pk=order.pk).update(**updates)


def _apply_fill_to_portfolio(order: BrokerOrder, fill: BrokerFill) -> None:
    """Mutate the broker Portfolio + write a LedgerEntry(kind=broker_fill).

    Sign convention: buy increases quantity, sell decreases (and may go
    negative if the position was a short already). cash_delta is negative
    for buys, positive for sells.
    """
    portfolio = order.broker_account.portfolio
    qty = _qty(fill.quantity)
    price = Decimal(str(fill.price))
    signed_qty = qty if order.side == "buy" else -qty
    cash_delta = -(qty * price) if order.side == "buy" else (qty * price)
    realized_pnl = Decimal("0")

    with transaction.atomic():
        portfolio = Portfolio.objects.select_for_update().get(pk=portfolio.pk)
        position = (
            Position.objects.select_for_update()
            .filter(portfolio=portfolio, ticker=order.ticker)
            .first()
        )
        kind = LedgerEntry.KIND_BROKER_FILL

        if position is None:
            position = Position.objects.create(
                portfolio=portfolio,
                ticker=order.ticker,
                quantity=signed_qty,
                avg_cost=price,
                sector="",
                opened_via=Position.OPENED_VIA_RUN,
                source_run=order.decision.run if order.decision else None,
                source_decision=order.decision,
                note=f"broker fill {fill.broker_fill_id}",
            )
        else:
            existing_is_long = position.quantity > 0
            adding_long = signed_qty > 0
            new_qty = position.quantity + signed_qty
            # Reducing or flipping: realize pnl on the reduced portion.
            if existing_is_long != adding_long:
                close_size = min(position.quantity.copy_abs(), qty)
                if existing_is_long:
                    realized_pnl = (price - position.avg_cost) * close_size
                else:
                    realized_pnl = (position.avg_cost - price) * close_size
                position.realized_pnl = (
                    position.realized_pnl + realized_pnl
                ).quantize(Decimal("0.01"))
            else:
                # Increasing — weighted-average new cost.
                old_abs = position.quantity.copy_abs()
                num = (old_abs * position.avg_cost) + (qty * price)
                den = old_abs + qty
                position.avg_cost = (num / den).quantize(Decimal("0.0001"))
            position.quantity = new_qty
            if position.quantity == 0:
                position.save(update_fields=["quantity", "avg_cost", "realized_pnl"])
                position.delete()
                position_for_ledger = None
            else:
                position.save(update_fields=["quantity", "avg_cost", "realized_pnl"])
                position_for_ledger = position
        position_for_ledger = (
            position if (position is not None and position.pk and position.quantity != 0)
            else None
        )

        portfolio.cash_balance = _money(portfolio.cash_balance + cash_delta)
        portfolio.save(update_fields=["cash_balance"])

        LedgerEntry.objects.create(
            portfolio=portfolio,
            kind=kind,
            ticker=order.ticker,
            quantity_delta=signed_qty,
            price=price,
            cash_delta=_money(cash_delta),
            realized_pnl=_money(realized_pnl),
            quantity_after=position_for_ledger.quantity if position_for_ledger else Decimal("0"),
            cash_balance_after=portfolio.cash_balance,
            position=position_for_ledger,
            source_run=order.decision.run if order.decision else None,
            source_decision=order.decision,
            broker_order=order,
            note=f"broker fill {fill.broker_fill_id}",
        )


# --- reconcile_account ------------------------------------------------------


@dataclass
class Drift:
    cash_delta: Decimal = Decimal("0")  # broker_cash - portfolio_cash
    # ticker -> broker_qty - portfolio_qty
    position_deltas: dict[str, Decimal] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def has_differences(self) -> bool:
        if abs(self.cash_delta) > Decimal("0.01"):
            return True
        return any(abs(v) > Decimal("0.000001") for v in self.position_deltas.values())

    def human_readable(self) -> str:
        parts = [f"cash drift: {self.cash_delta:+.2f}"]
        for ticker, delta in self.position_deltas.items():
            if abs(delta) > Decimal("0.000001"):
                parts.append(f"{ticker} qty drift: {delta:+}")
        parts.extend(self.notes)
        return "; ".join(parts)


def compute_drift(
    *,
    broker_positions: Iterable[PositionSnapshot],
    broker_cash: Decimal,
    portfolio: Portfolio,
) -> Drift:
    drift = Drift(cash_delta=_money(broker_cash) - _money(portfolio.cash_balance))
    by_ticker = {p.ticker.upper(): Decimal(str(p.quantity)) for p in broker_positions}
    local_qty = {
        pos.ticker.upper(): Decimal(str(pos.quantity))
        for pos in portfolio.positions.all()
    }
    tickers = set(by_ticker.keys()) | set(local_qty.keys())
    for ticker in tickers:
        broker_qty = by_ticker.get(ticker, Decimal("0"))
        local = local_qty.get(ticker, Decimal("0"))
        delta = broker_qty - local
        if abs(delta) > Decimal("0.000001"):
            drift.position_deltas[ticker] = delta
    return drift


def apply_drift_as_ledger_entries(
    *, drift: Drift, portfolio: Portfolio, event: BrokerSyncEvent,
    broker_positions: dict[str, PositionSnapshot],
) -> int:
    """Square the broker portfolio with the broker side.

    Writes `reconciliation_adjustment` LedgerEntry rows so the ledger
    invariant holds. Returns the number of rows written.
    """
    written = 0
    with transaction.atomic():
        portfolio = Portfolio.objects.select_for_update().get(pk=portfolio.pk)
        # Position drift: bring local qty to broker qty.
        for ticker, delta in drift.position_deltas.items():
            broker_pos = broker_positions.get(ticker)
            new_avg_cost = (
                Decimal(str(broker_pos.avg_cost))
                if broker_pos is not None
                else Decimal("0")
            )
            position = Position.objects.select_for_update().filter(
                portfolio=portfolio, ticker=ticker,
            ).first()
            if position is None:
                if delta == 0:
                    continue
                position = Position.objects.create(
                    portfolio=portfolio,
                    ticker=ticker,
                    quantity=delta,
                    avg_cost=new_avg_cost or Decimal("100"),
                    sector="",
                    opened_via=Position.OPENED_VIA_RUN,
                    note="reconciliation_adjustment",
                )
                qty_after = position.quantity
            else:
                position.quantity = position.quantity + delta
                if broker_pos is not None and new_avg_cost > 0:
                    position.avg_cost = new_avg_cost
                if position.quantity == 0:
                    position.save(update_fields=["quantity", "avg_cost"])
                    position.delete()
                    qty_after = Decimal("0")
                    position = None
                else:
                    position.save(update_fields=["quantity", "avg_cost"])
                    qty_after = position.quantity

            LedgerEntry.objects.create(
                portfolio=portfolio,
                kind=LedgerEntry.KIND_RECONCILE,
                ticker=ticker,
                quantity_delta=delta,
                price=new_avg_cost or None,
                cash_delta=Decimal("0"),  # cash drift is recorded separately below
                realized_pnl=Decimal("0"),
                quantity_after=qty_after,
                cash_balance_after=_money(portfolio.cash_balance),
                position=position,
                broker_sync_event=event,
                note="reconciliation_adjustment (position)",
            )
            written += 1

        if abs(drift.cash_delta) > Decimal("0.01"):
            portfolio.cash_balance = _money(
                portfolio.cash_balance + drift.cash_delta
            )
            portfolio.save(update_fields=["cash_balance"])
            LedgerEntry.objects.create(
                portfolio=portfolio,
                kind=LedgerEntry.KIND_RECONCILE,
                ticker="",
                quantity_delta=Decimal("0"),
                price=None,
                cash_delta=_money(drift.cash_delta),
                realized_pnl=Decimal("0"),
                quantity_after=None,
                cash_balance_after=_money(portfolio.cash_balance),
                broker_sync_event=event,
                note="reconciliation_adjustment (cash)",
            )
            written += 1

    return written


def reconcile_account(
    account: BrokerAccount,
    *,
    triggered_by: str = BrokerSyncEvent.TRIGGER_PERIODIC,
) -> BrokerSyncEvent:
    # Always refetch — the BrokerAccount + Portfolio passed in by a view
    # may carry stale cash_balance loaded before the post-confirm fill
    # ingestion ran. We need the latest persisted state to compute drift.
    account = BrokerAccount.objects.select_related("portfolio").get(pk=account.pk)

    cap = get_capabilities(account.broker)

    # P3a-2 amendment (ADR 0011): skip a credentialed account whose
    # connection_status is not active (needs_reauth, disabled, error,
    # connecting). The demo broker has no remote session to fail, so it
    # is always considered available regardless of its row state.
    # Without this check, a view-triggered sync on a needs_reauth IBKR
    # account would call the dead gateway and surface a confusing 502.
    is_demo = cap is not None and cap.auth_kind == AUTH_NONE
    if not is_demo and account.connection_status != BrokerAccount.STATUS_ACTIVE:
        event = BrokerSyncEvent.objects.create(
            broker_account=account,
            triggered_by=triggered_by,
            started_at=timezone.now(),
        )
        event.finished_at = timezone.now()
        event.notes = (
            f"skipped — account is {account.connection_status}; "
            "re-authenticate to resume reconciliation"
        )
        event.save(update_fields=["finished_at", "notes"])
        return event

    # The demo broker has no external venue to reconcile against — the
    # database *is* its book of record. A "sync" therefore re-checks
    # resting limit/stop orders so a manual "Sync now" (or the periodic
    # job) fills anything the market has since crossed.
    if is_demo:
        from .demo_fills import evaluate_resting_demo_orders  # local: avoid cycle

        event = BrokerSyncEvent.objects.create(
            broker_account=account,
            triggered_by=triggered_by,
            started_at=timezone.now(),
        )
        filled = evaluate_resting_demo_orders(account)
        event.finished_at = timezone.now()
        event.notes = (
            f"demo re-check — {filled} resting order(s) filled"
            if filled
            else "demo re-check — no resting orders triggered"
        )
        event.save(update_fields=["finished_at", "notes"])
        BrokerAccount.objects.filter(pk=account.pk).update(
            last_synced_at=timezone.now(),
        )
        return event

    broker = get_broker(account)
    event = BrokerSyncEvent.objects.create(
        broker_account=account,
        triggered_by=triggered_by,
        started_at=timezone.now(),
    )
    try:
        broker_positions = broker.get_positions()
        broker_account = broker.get_account()
        broker_cash = Decimal(str(broker_account.cash))
    except BrokerError as exc:
        event.finished_at = timezone.now()
        event.error_message = str(exc)[:500]
        event.save(update_fields=["finished_at", "error_message"])
        return event

    drift = compute_drift(
        broker_positions=broker_positions,
        broker_cash=broker_cash,
        portfolio=account.portfolio,
    )
    written = 0
    if drift.has_differences:
        broker_positions_by_ticker = {p.ticker.upper(): p for p in broker_positions}
        written = apply_drift_as_ledger_entries(
            drift=drift,
            portfolio=account.portfolio,
            event=event,
            broker_positions=broker_positions_by_ticker,
        )

    event.drift_detected = drift.has_differences
    event.ledger_entries_written = written
    event.finished_at = timezone.now()
    event.notes = drift.human_readable() if drift.has_differences else ""
    event.save(update_fields=[
        "drift_detected", "ledger_entries_written", "finished_at", "notes",
    ])

    # Update the account header.
    BrokerAccount.objects.filter(pk=account.pk).update(last_synced_at=timezone.now())
    if drift.has_differences:
        BrokerAccount.objects.filter(pk=account.pk).update(last_drift_event=event)
    return event


# --- broken-leg group submit (P2k hook) -------------------------------------


def submit_order_group(*, orders: list[BrokerOrder], broker: Broker | None = None,
                       confirm_ctx=None) -> dict:
    """Submit a list of paired orders sequentially. If any leg fails,
    the rest are NOT submitted automatically — the group is flagged
    `partially_submitted` and surfaced for explicit user resolution."""
    from .idempotency import submit_idempotent  # local to avoid cycles

    if not orders:
        return {"submitted": 0, "remaining": 0, "status": "empty"}
    group_id = uuid.uuid4()
    BrokerOrder.objects.filter(pk__in=[o.pk for o in orders]).update(group_id=group_id)

    submitted = 0
    for order in orders:
        order.refresh_from_db()
        adapter = broker or get_broker(order.broker_account)
        try:
            submit_idempotent(order=order, broker=adapter)
            submitted += 1
        except Exception as exc:
            return {
                "group_id": str(group_id),
                "submitted": submitted,
                "remaining": len(orders) - submitted,
                "status": "partially_submitted",
                "error": str(exc),
            }
    return {
        "group_id": str(group_id),
        "submitted": submitted,
        "remaining": 0,
        "status": "submitted",
    }
