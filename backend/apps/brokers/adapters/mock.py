"""MockBroker — the "Demo broker (no real account)" adapter.

In-memory state lives in `_STATE`, keyed by (user_id, account_id) so two
demo accounts in the same user can hold independent positions. State is
deliberately process-local: it's a developer fixture, not a database. A
restart loses pending fills, which is fine since reconcile_account will
realign the broker portfolio to whatever the adapter currently reports.

Configurable knobs are read from `BrokerAccount.config`:
  - slippage_bps : int    apply ±N bps to the fill price (default 0)
  - latency_ms   : int    delay between submit and the first fill (default 0)
  - partial_fill : bool   split the fill into two pieces (default False)
  - reject       : bool   immediately reject the next submit (default False)
  - simulate_drift : dict optional one-shot drift to apply on next sync
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from django.utils import timezone

from ..capabilities import (
    AUTH_NONE,
    BrokerCapabilities,
    register_broker,
)
from ..interfaces import (
    AccountSnapshot,
    Broker,
    BrokerError,
    FillSnapshot,
    OrderSnapshot,
    OrderTicket,
    PositionSnapshot,
)

CAPABILITIES = BrokerCapabilities(
    code="mock",
    display_name="Demo broker (no real account)",
    auth_kind=AUTH_NONE,
    supports_paper=True,
    supports_live=False,
    supports_fractional=True,
    quantity_increment=Decimal("0.0001"),
    supported_order_types=("market", "limit", "stop"),
    supported_time_in_force=("day", "gtc"),
    description=(
        "An in-memory paper broker so you can walk through the order "
        "lifecycle end-to-end without a real brokerage."
    ),
    available=True,
)


# --- in-memory state --------------------------------------------------------


@dataclass
class _BrokerOrder:
    broker_order_id: str
    client_order_id: str
    ticker: str
    side: str
    quantity: Decimal
    order_type: str
    limit_price: Decimal | None
    time_in_force: str
    status: str
    filled_quantity: Decimal = Decimal("0")
    avg_fill_price: Decimal | None = None
    queued_until_open: bool = False
    submitted_at: datetime | None = None
    fills: list[FillSnapshot] = field(default_factory=list)
    pending_fill_ticks: int = 0  # remaining poll() ticks until the next fill chunk
    partial_done: bool = False


@dataclass
class _AccountState:
    cash: Decimal = Decimal("100000")
    # ticker -> (qty, avg_cost)
    positions: dict[str, tuple[Decimal, Decimal]] = field(default_factory=dict)
    orders: dict[str, _BrokerOrder] = field(default_factory=dict)
    # client_order_id -> broker_order_id
    client_id_index: dict[str, str] = field(default_factory=dict)


_STATE: dict[tuple[int, str], _AccountState] = {}
_LOCK = threading.Lock()


def _state(key: tuple[int, str]) -> _AccountState:
    with _LOCK:
        if key not in _STATE:
            _STATE[key] = _AccountState()
        return _STATE[key]


def reset_state() -> None:
    """Test helper. Drops every demo book."""
    with _LOCK:
        _STATE.clear()


# --- adapter ----------------------------------------------------------------


class MockBroker(Broker):
    capabilities = CAPABILITIES

    def __init__(self, account) -> None:
        self._account = account
        self._key = (account.user_id, account.account_id)
        self._state = _state(self._key)
        cfg = account.config or {}
        self._slippage_bps = int(cfg.get("slippage_bps", 0))
        self._latency_ticks = max(0, int(cfg.get("latency_ticks", 0)))
        self._partial_fill = bool(cfg.get("partial_fill", False))
        self._reject_next = bool(cfg.get("reject_next", False))
        self._simulate_drift = cfg.get("simulate_drift") or {}

    # ----- read-only -----

    def get_account(self) -> AccountSnapshot:
        return AccountSnapshot(
            account_id=self._account.account_id,
            cash=self._state.cash,
            buying_power=self._state.cash,
            equity=self._state.cash + self._mtm(),
            currency=self._account.base_currency,
        )

    def _mtm(self) -> Decimal:
        total = Decimal("0")
        for _, (qty, cost) in self._state.positions.items():
            total += qty * cost  # marks-at-cost is fine for the demo
        return total

    def get_positions(self) -> list[PositionSnapshot]:
        applied_drift = self._consume_drift()
        out: list[PositionSnapshot] = []
        for ticker, (qty, cost) in self._state.positions.items():
            if qty == 0:
                continue
            out.append(PositionSnapshot(ticker=ticker, quantity=qty, avg_cost=cost))
        if applied_drift:
            # Force the caller to refresh on next pass too.
            pass
        return out

    def get_recent_fills(self, since: datetime) -> list[FillSnapshot]:
        rows: list[FillSnapshot] = []
        for order in self._state.orders.values():
            for fill in order.fills:
                if fill.filled_at >= since:
                    rows.append(fill)
        return rows

    # ----- mutations -----

    def submit_order(self, ticket: OrderTicket) -> OrderSnapshot:
        # Dedup on client_order_id — broker side of idempotency.
        existing_id = self._state.client_id_index.get(ticket.client_order_id)
        if existing_id is not None:
            return self._to_snapshot(self._state.orders[existing_id])

        if self._reject_next:
            self._reject_next = False
            raise BrokerError("MockBroker configured to reject next submission")

        broker_id = f"MOCK-{uuid.uuid4().hex[:12]}"
        order = _BrokerOrder(
            broker_order_id=broker_id,
            client_order_id=ticket.client_order_id,
            ticker=ticket.ticker.upper(),
            side=ticket.side,
            quantity=Decimal(str(ticket.quantity)),
            order_type=ticket.order_type,
            limit_price=ticket.limit_price,
            time_in_force=ticket.time_in_force,
            status="submitted",
            submitted_at=timezone.now(),
            pending_fill_ticks=self._latency_ticks,
        )
        self._state.orders[broker_id] = order
        self._state.client_id_index[ticket.client_order_id] = broker_id

        # If no latency configured, fill immediately so a simple test sees the
        # full lifecycle without polling.
        if self._latency_ticks == 0:
            self._advance_fill(order)
        return self._to_snapshot(order)

    def cancel_order(self, broker_order_id: str) -> None:
        order = self._state.orders.get(broker_order_id)
        if order is None:
            raise BrokerError("unknown order")
        if order.status in ("filled", "cancelled", "rejected", "error"):
            return  # idempotent cancel
        order.status = "cancelled"

    def get_order(self, broker_order_id: str) -> OrderSnapshot:
        order = self._state.orders.get(broker_order_id)
        if order is None:
            raise BrokerError("unknown order")
        # Each poll consumes one latency tick. When the counter hits zero we
        # fill (or partial-fill) the order.
        if order.status == "submitted" and order.pending_fill_ticks > 0:
            order.pending_fill_ticks -= 1
            if order.pending_fill_ticks == 0:
                self._advance_fill(order)
        elif order.status == "partial":
            # Second fill arrives on the next poll after the first partial.
            self._advance_fill(order)
        return self._to_snapshot(order)

    def find_order_by_client_id(
        self, client_order_id: str, *, order_meta=None,
    ) -> OrderSnapshot | None:
        broker_id = self._state.client_id_index.get(client_order_id)
        if broker_id is None:
            return None
        return self._to_snapshot(self._state.orders[broker_id])

    # ----- internals -----

    def _advance_fill(self, order: _BrokerOrder) -> None:
        remaining = order.quantity - order.filled_quantity
        if remaining <= 0:
            order.status = "filled"
            return
        if self._partial_fill and not order.partial_done:
            chunk = (remaining / Decimal("2")).quantize(Decimal("0.0001"))
            if chunk <= 0:
                chunk = remaining
            order.partial_done = True
        else:
            chunk = remaining

        price = self._price_for(order)
        fill = FillSnapshot(
            broker_fill_id=f"FILL-{uuid.uuid4().hex[:10]}",
            broker_order_id=order.broker_order_id,
            ticker=order.ticker,
            quantity=chunk,
            price=price,
            filled_at=timezone.now(),
            side=order.side,
        )
        order.fills.append(fill)
        order.filled_quantity = order.filled_quantity + chunk
        # Recompute volume-weighted avg fill price.
        if order.fills:
            num = sum(
                (Decimal(str(f.price)) * Decimal(str(f.quantity)) for f in order.fills),
                Decimal("0"),
            )
            den = sum(
                (Decimal(str(f.quantity)) for f in order.fills),
                Decimal("0"),
            )
            order.avg_fill_price = (num / den).quantize(Decimal("0.0001"))

        if order.filled_quantity >= order.quantity:
            order.status = "filled"
        else:
            order.status = "partial"

        # Update positions + cash.
        signed_qty = chunk if order.side == "buy" else -chunk
        cur_qty, cur_cost = self._state.positions.get(
            order.ticker, (Decimal("0"), Decimal("0")),
        )
        new_qty = cur_qty + signed_qty
        if cur_qty == 0:
            new_cost = price
        elif (cur_qty > 0 and signed_qty > 0) or (cur_qty < 0 and signed_qty < 0):
            num = (cur_qty.copy_abs() * cur_cost) + (chunk * price)
            den = cur_qty.copy_abs() + chunk
            new_cost = (num / den).quantize(Decimal("0.0001"))
        else:
            # Reducing or flipping. Keep prior cost; the framework computes pnl.
            new_cost = cur_cost
        if new_qty == 0:
            self._state.positions.pop(order.ticker, None)
        else:
            self._state.positions[order.ticker] = (new_qty, new_cost)
        if order.side == "buy":
            self._state.cash = self._state.cash - (chunk * price)
        else:
            self._state.cash = self._state.cash + (chunk * price)

    def _price_for(self, order: _BrokerOrder) -> Decimal:
        # If a limit price is set, fill at limit; otherwise pick a deterministic
        # "market" price from the ticker hash so successive fills are stable
        # within a run.
        base = order.limit_price or Decimal(
            str(50 + (abs(hash(order.ticker)) % 200))
        )
        base = base.quantize(Decimal("0.0001"))
        if self._slippage_bps:
            slip = base * Decimal(self._slippage_bps) / Decimal("10000")
            sign = Decimal("1") if order.side == "buy" else Decimal("-1")
            base = (base + sign * slip).quantize(Decimal("0.0001"))
        return base

    def _to_snapshot(self, order: _BrokerOrder) -> OrderSnapshot:
        return OrderSnapshot(
            broker_order_id=order.broker_order_id,
            client_order_id=order.client_order_id,
            ticker=order.ticker,
            side=order.side,
            quantity=order.quantity,
            order_type=order.order_type,
            limit_price=order.limit_price,
            time_in_force=order.time_in_force,
            status=order.status,
            filled_quantity=order.filled_quantity,
            avg_fill_price=order.avg_fill_price,
            queued_until_open=order.queued_until_open,
        )

    def _consume_drift(self) -> bool:
        """Apply (and zero) any pending simulated drift from `config`."""
        if not self._simulate_drift:
            return False
        applied = False
        for ticker, qty in dict(self._simulate_drift).items():
            try:
                qty_d = Decimal(str(qty))
            except (ValueError, ArithmeticError):
                continue
            cur_qty, cur_cost = self._state.positions.get(
                ticker, (Decimal("0"), Decimal("100")),
            )
            new_qty = cur_qty + qty_d
            if new_qty == 0:
                self._state.positions.pop(ticker, None)
            else:
                self._state.positions[ticker] = (new_qty, cur_cost)
            applied = True
        self._simulate_drift = {}
        # Persist removal so subsequent calls don't replay it.
        cfg = dict(self._account.config or {})
        cfg.pop("simulate_drift", None)
        self._account.config = cfg
        try:
            self._account.save(update_fields=["config"])
        except Exception:  # pragma: no cover - in tests the row may not be saved
            pass
        return applied


def _factory(account) -> MockBroker:
    return MockBroker(account)


register_broker(CAPABILITIES, _factory)


def configure_account(account, **knobs) -> None:
    """Test/dev helper: mutate the in-memory state directly for a given account."""
    state = _state((account.user_id, account.account_id))
    if "cash" in knobs:
        state.cash = Decimal(str(knobs["cash"]))
    if "positions" in knobs:
        state.positions = {
            ticker: (Decimal(str(qty)), Decimal(str(cost)))
            for ticker, (qty, cost) in knobs["positions"].items()
        }


def seed_demo_book(account, *, cash: Decimal = Decimal("100000")) -> None:
    """Initialize the demo book with the user's chosen starting cash."""
    state = _state((account.user_id, account.account_id))
    state.cash = cash
    state.positions = {}
