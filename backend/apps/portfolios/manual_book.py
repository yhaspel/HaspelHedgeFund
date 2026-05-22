"""P3: Manual Book service — the per-user paper book.

All mutations wrap a ``transaction.atomic()`` block that:
  1. Locks the Portfolio row with ``select_for_update()`` so two browser
     tabs can't open the same ticker concurrently.
  2. Locks the affected Position row if one exists.
  3. Updates cash_balance and Position quantity/avg_cost.
  4. Writes a single ``LedgerEntry`` so the ledger reconciles:
     initial_cash + Σ cash_delta = current cash_balance.

The service exposes plain Python entry points so the API view layer
(``apps/portfolios/views.py``) is a thin wrapper that handles auth and
serialization.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.db import IntegrityError, transaction

from .models import LedgerEntry, Portfolio, Position
from .quantity_policy import QuantityPolicy, validate_quantity_for_mode

INITIAL_CASH = Decimal("100000")
MANUAL_BOOK_NAME = "Manual Book"


class ManualBookError(ValueError):
    """User-facing error raised by the manual-book service.

    Carries a status_code so the view layer can return 400/409/etc without
    creating a custom DRF exception hierarchy. Default 400.
    """

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _money(x: Decimal | float | int) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def get_or_create_manual_book(user: Any) -> Portfolio:
    """Lazy-create the Manual Book on first access.

    The DB-side partial unique constraint guarantees at most one
    ``kind="manual"`` row per user even under concurrent first-visit
    requests. The except-IntegrityError branch handles the rare race.
    """
    existing = Portfolio.objects.filter(
        user=user, kind=Portfolio.KIND_MANUAL,
    ).first()
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            portfolio = Portfolio.objects.create(
                user=user,
                name=MANUAL_BOOK_NAME,
                kind=Portfolio.KIND_MANUAL,
                cash_balance=INITIAL_CASH,
            )
            LedgerEntry.objects.create(
                portfolio=portfolio,
                kind=LedgerEntry.KIND_DEPOSIT,
                cash_delta=INITIAL_CASH,
                cash_balance_after=INITIAL_CASH,
                created_by=user,
                note="Initial paper-book funding ($100,000).",
            )
            return portfolio
    except IntegrityError:
        return Portfolio.objects.get(user=user, kind=Portfolio.KIND_MANUAL)


def _reserved_short_proceeds(portfolio: Portfolio) -> Decimal:
    total = Decimal("0")
    for pos in portfolio.positions.all():
        if pos.quantity < 0:
            total += pos.quantity.copy_abs() * pos.avg_cost
    return _money(total)


def _free_cash(portfolio: Portfolio) -> Decimal:
    return _money(portfolio.cash_balance - _reserved_short_proceeds(portfolio))


@dataclass
class OpenPositionResult:
    position: Position
    ledger_entry: LedgerEntry
    portfolio: Portfolio


def open_or_increase_position(
    *,
    user: Any,
    ticker: str,
    side: str,
    quantity: Decimal,
    entry_price: Decimal,
    quantity_mode: str = "whole",
    source_run: Any | None = None,
    source_decision: Any | None = None,
    note: str = "",
) -> OpenPositionResult:
    if side not in ("long", "short"):
        raise ManualBookError("side must be 'long' or 'short'")
    if entry_price is None or Decimal(str(entry_price)) <= 0:
        raise ManualBookError("entry_price must be positive")
    policy = QuantityPolicy.from_mode(quantity_mode)
    try:
        q_abs = validate_quantity_for_mode(quantity, policy)
    except ValueError as exc:
        raise ManualBookError(str(exc)) from exc
    if q_abs <= 0:
        raise ManualBookError("quantity must be positive")
    price = Decimal(str(entry_price)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP,
    )
    ticker_norm = ticker.strip().upper()
    if not ticker_norm:
        raise ManualBookError("ticker is required")

    # Provenance must be consistent and belong to this user.
    if source_decision is not None:
        if source_run is None or source_decision.run_id != source_run.id:
            raise ManualBookError("source_decision must belong to source_run")
    if source_run is not None:
        if source_run.user_id != getattr(user, "id", None):
            raise ManualBookError("source_run does not belong to this user")
        if source_run.status != "done":
            raise ManualBookError(
                "source_run must be in 'done' status to enter a position"
            )

    # Ensure the manual book exists before opening the locking atomic block.
    # Auto-creation owns its own transaction, so doing it inside the lock
    # block would deadlock against the partial-unique constraint check.
    get_or_create_manual_book(user)

    with transaction.atomic():
        portfolio = (
            Portfolio.objects.select_for_update()
            .get(user=user, kind=Portfolio.KIND_MANUAL)
        )
        existing = (
            Position.objects.select_for_update()
            .filter(portfolio=portfolio, ticker=ticker_norm)
            .first()
        )

        if existing is not None:
            existing_is_long = existing.quantity > 0
            request_is_long = side == "long"
            if existing_is_long != request_is_long:
                raise ManualBookError(
                    f"{ticker_norm} already exists on the opposite side; close "
                    "or reduce that position before opening the other side.",
                    status_code=409,
                )

        signed_qty = q_abs if side == "long" else -q_abs

        if side == "long":
            cash_needed = q_abs * price
            available = portfolio.cash_balance - _reserved_short_proceeds(portfolio)
            if available + Decimal("0.005") < cash_needed:
                raise ManualBookError(
                    f"Insufficient free cash. Need ${cash_needed:.2f}, "
                    f"have ${available:.2f} free.",
                )
            cash_delta = -cash_needed
            entry_kind = (
                LedgerEntry.KIND_INCREASE if existing is not None
                else LedgerEntry.KIND_OPEN
            )
        else:  # short
            cash_delta = q_abs * price  # proceeds credited
            entry_kind = (
                LedgerEntry.KIND_INCREASE if existing is not None
                else LedgerEntry.KIND_OPEN
            )

        if existing is not None:
            old_qty_abs = existing.quantity.copy_abs()
            new_qty_abs = old_qty_abs + q_abs
            existing.avg_cost = (
                (existing.avg_cost * old_qty_abs + price * q_abs) / new_qty_abs
            ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            existing.quantity = (existing.quantity + signed_qty)
            if note and not existing.note:
                existing.note = note
            existing.save(update_fields=["quantity", "avg_cost", "note"])
            position = existing
        else:
            opened_via = (
                Position.OPENED_VIA_RUN if source_run is not None
                else Position.OPENED_VIA_MANUAL
            )
            position = Position.objects.create(
                portfolio=portfolio,
                ticker=ticker_norm,
                quantity=signed_qty,
                avg_cost=price,
                sector="",
                opened_via=opened_via,
                source_run=source_run,
                source_decision=source_decision,
                note=note,
            )

        portfolio.cash_balance = _money(portfolio.cash_balance + cash_delta)
        portfolio.save(update_fields=["cash_balance"])

        ledger = LedgerEntry.objects.create(
            portfolio=portfolio,
            kind=entry_kind,
            ticker=ticker_norm,
            quantity_delta=signed_qty,
            price=price,
            cash_delta=_money(cash_delta),
            realized_pnl=Decimal("0"),
            quantity_after=position.quantity,
            cash_balance_after=portfolio.cash_balance,
            position=position,
            source_run=source_run,
            source_decision=source_decision,
            note=note,
            created_by=user,
        )

    return OpenPositionResult(position=position, ledger_entry=ledger, portfolio=portfolio)


@dataclass
class CloseResult:
    portfolio: Portfolio
    ledger_entry: LedgerEntry
    position: Position | None  # None when fully closed
    realized_pnl: Decimal


def close_or_reduce_position(
    *,
    user: Any,
    position_id: int,
    exit_price: Decimal,
    quantity: Decimal | None = None,
    quantity_mode: str = "whole",
    note: str = "",
) -> CloseResult:
    """Close (default: full size) or partially close a position.

    Realized P&L formulae (Decimal, per the phase plan):
      - Long  close: pnl = (exit - avg_cost) * closed_qty; cash += closed_qty * exit
      - Short close: pnl = (avg_cost - exit) * closed_qty; cash -= closed_qty * exit

    Full close deletes the Position row but the LedgerEntry retains the
    realized_pnl + quantity_after=0 audit trail.
    """
    if exit_price is None or Decimal(str(exit_price)) <= 0:
        raise ManualBookError("exit_price must be positive")
    price = Decimal(str(exit_price)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP,
    )
    policy = QuantityPolicy.from_mode(quantity_mode)
    get_or_create_manual_book(user)

    with transaction.atomic():
        portfolio = (
            Portfolio.objects.select_for_update()
            .filter(user=user, kind=Portfolio.KIND_MANUAL).first()
        )
        if portfolio is None:
            raise ManualBookError("manual book not found", status_code=404)
        try:
            position = (
                Position.objects.select_for_update()
                .get(id=position_id, portfolio=portfolio)
            )
        except Position.DoesNotExist as exc:
            raise ManualBookError("position not found", status_code=404) from exc

        stored_abs = position.quantity.copy_abs()
        is_short = position.quantity < 0

        if quantity is None:
            close_abs = stored_abs
            is_full_close = True
        else:
            try:
                close_abs = validate_quantity_for_mode(
                    Decimal(str(quantity)).copy_abs(), policy,
                )
            except ValueError as exc:
                raise ManualBookError(str(exc)) from exc
            if close_abs <= 0:
                raise ManualBookError("quantity must be positive")
            # A full close is permitted to match the stored quantity even if
            # it's fractional under whole-share policy.
            if close_abs == stored_abs:
                is_full_close = True
            elif close_abs > stored_abs:
                raise ManualBookError(
                    f"Close size {close_abs} exceeds position quantity {stored_abs}",
                )
            else:
                is_full_close = False

        if is_short:
            realized_pnl = (position.avg_cost - price) * close_abs
            cash_delta = -(close_abs * price)
        else:
            realized_pnl = (price - position.avg_cost) * close_abs
            cash_delta = close_abs * price

        realized_pnl = realized_pnl.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        cash_delta = _money(cash_delta)

        new_cash = _money(portfolio.cash_balance + cash_delta)
        # Cash-floor invariant: cover-short losses cannot drive cash negative.
        if new_cash < -Decimal("0.005"):
            raise ManualBookError(
                "Closing this position would drive cash below zero. "
                "Deposit cash before closing.",
            )

        portfolio.cash_balance = new_cash
        portfolio.save(update_fields=["cash_balance"])

        # Position lifecycle.
        if is_full_close:
            kind = LedgerEntry.KIND_CLOSE
            quantity_after: Decimal | None = Decimal("0")
            # Record realized P&L on the position before deleting (so we can
            # accumulate it in the per-ticker history if we later restore the
            # row; for now we drop the row and rely on the ledger).
            position.realized_pnl = (position.realized_pnl + realized_pnl).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )
            position.save(update_fields=["realized_pnl"])
            position_for_ledger = position
            position.delete()
            position_obj: Position | None = None
        else:
            kind = LedgerEntry.KIND_REDUCE
            if is_short:
                position.quantity = position.quantity + close_abs  # toward zero
            else:
                position.quantity = position.quantity - close_abs
            position.realized_pnl = (position.realized_pnl + realized_pnl).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )
            position.save(update_fields=["quantity", "realized_pnl"])
            quantity_after = position.quantity
            position_for_ledger = position
            position_obj = position

        ledger = LedgerEntry.objects.create(
            portfolio=portfolio,
            kind=kind,
            ticker=position_for_ledger.ticker,
            quantity_delta=(close_abs if is_short else -close_abs),
            price=price,
            cash_delta=cash_delta,
            realized_pnl=realized_pnl,
            quantity_after=quantity_after,
            cash_balance_after=portfolio.cash_balance,
            position=position_obj,
            source_run=position_for_ledger.source_run,
            source_decision=position_for_ledger.source_decision,
            note=note,
            created_by=user,
        )

    return CloseResult(
        portfolio=portfolio,
        ledger_entry=ledger,
        position=position_obj,
        realized_pnl=realized_pnl,
    )


@dataclass
class EditResult:
    position: Position
    ledger_entry: LedgerEntry
    portfolio: Portfolio


def edit_position(
    *,
    user: Any,
    position_id: int,
    quantity: Decimal | None = None,
    avg_cost: Decimal | None = None,
    note: str | None = None,
    quantity_mode: str = "whole",
) -> EditResult:
    """Correction-only edit. Does NOT change cash; writes an edit_adjustment
    ledger row with before/after snapshots.

    Economic trades belong in open/increase/close.
    """
    if quantity is None and avg_cost is None and note is None:
        raise ManualBookError("nothing to edit")

    policy = QuantityPolicy.from_mode(quantity_mode)
    get_or_create_manual_book(user)
    with transaction.atomic():
        portfolio = (
            Portfolio.objects.select_for_update()
            .filter(user=user, kind=Portfolio.KIND_MANUAL).first()
        )
        if portfolio is None:
            raise ManualBookError("manual book not found", status_code=404)
        try:
            position = (
                Position.objects.select_for_update()
                .get(id=position_id, portfolio=portfolio)
            )
        except Position.DoesNotExist as exc:
            raise ManualBookError("position not found", status_code=404) from exc

        before = {
            "quantity": str(position.quantity),
            "avg_cost": str(position.avg_cost),
            "note": position.note,
        }

        if quantity is not None:
            try:
                # Validate magnitude; preserve sign relative to existing side.
                new_q = Decimal(str(quantity))
                is_short = position.quantity < 0
                validate_quantity_for_mode(new_q.copy_abs(), policy)
            except ValueError as exc:
                raise ManualBookError(str(exc)) from exc
            if new_q == 0:
                raise ManualBookError(
                    "quantity 0 is not a valid edit; close the position instead"
                )
            # Sign convention: same side as existing position, magnitude=|new_q|.
            position.quantity = -new_q.copy_abs() if is_short else new_q.copy_abs()
        if avg_cost is not None:
            ac = Decimal(str(avg_cost))
            if ac <= 0:
                raise ManualBookError("avg_cost must be positive")
            position.avg_cost = ac.quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP,
            )
        if note is not None:
            position.note = note

        position.save(update_fields=["quantity", "avg_cost", "note"])

        after = {
            "quantity": str(position.quantity),
            "avg_cost": str(position.avg_cost),
            "note": position.note,
        }

        ledger = LedgerEntry.objects.create(
            portfolio=portfolio,
            kind=LedgerEntry.KIND_EDIT,
            ticker=position.ticker,
            quantity_delta=Decimal("0"),
            price=None,
            cash_delta=Decimal("0"),
            realized_pnl=Decimal("0"),
            quantity_after=position.quantity,
            cash_balance_after=portfolio.cash_balance,
            position=position,
            note=(note or "") + f" before={before} after={after}",
            created_by=user,
        )

    return EditResult(position=position, ledger_entry=ledger, portfolio=portfolio)


@dataclass
class CashAdjustResult:
    portfolio: Portfolio
    ledger_entry: LedgerEntry


def adjust_cash(
    *,
    user: Any,
    kind: str,
    amount: Decimal,
    note: str = "",
) -> CashAdjustResult:
    if kind not in (LedgerEntry.KIND_DEPOSIT, LedgerEntry.KIND_WITHDRAWAL):
        raise ManualBookError("kind must be 'deposit' or 'withdrawal'")
    amt = Decimal(str(amount))
    if amt <= 0:
        raise ManualBookError("amount must be positive")
    amt = amt.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    get_or_create_manual_book(user)

    with transaction.atomic():
        portfolio = (
            Portfolio.objects.select_for_update()
            .filter(user=user, kind=Portfolio.KIND_MANUAL).first()
        )
        if portfolio is None:
            raise ManualBookError("manual book not found", status_code=404)

        if kind == LedgerEntry.KIND_DEPOSIT:
            cash_delta = amt
        else:
            # Withdrawals cannot consume reserved short proceeds.
            available = portfolio.cash_balance - _reserved_short_proceeds(portfolio)
            if amt > available:
                raise ManualBookError(
                    f"Withdrawal of ${amt:.2f} exceeds free cash of "
                    f"${available:.2f}.",
                )
            cash_delta = -amt

        portfolio.cash_balance = _money(portfolio.cash_balance + cash_delta)
        portfolio.save(update_fields=["cash_balance"])

        ledger = LedgerEntry.objects.create(
            portfolio=portfolio,
            kind=kind,
            cash_delta=cash_delta,
            cash_balance_after=portfolio.cash_balance,
            note=note,
            created_by=user,
        )
    return CashAdjustResult(portfolio=portfolio, ledger_entry=ledger)
