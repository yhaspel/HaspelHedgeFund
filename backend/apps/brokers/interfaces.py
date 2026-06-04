"""Broker Protocol + framework DTOs (P3a-1).

Every adapter returns these dataclasses — never broker-SDK objects — so the
rest of the system (confirmation gate, reconciliation, idempotency, views,
frontend) is genuinely broker-agnostic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from .capabilities import BrokerCapabilities


@dataclass(frozen=True)
class AccountSnapshot:
    account_id: str
    cash: Decimal
    buying_power: Decimal
    equity: Decimal
    currency: str = "USD"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PositionSnapshot:
    ticker: str
    quantity: Decimal      # signed: negative = short
    avg_cost: Decimal
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FillSnapshot:
    broker_fill_id: str
    broker_order_id: str
    ticker: str
    quantity: Decimal
    price: Decimal
    filled_at: datetime
    side: str = "buy"          # "buy" | "sell"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderTicket:
    client_order_id: str
    ticker: str
    side: str                  # "buy" | "sell"
    quantity: Decimal
    # "market" | "limit" | "stop" | "stop_limit" | "trailing_stop"
    order_type: str = "market"
    limit_price: Decimal | None = None
    time_in_force: str = "day"  # "day" | "gtc"
    # NEW (P3a) — standalone stop / trailing:
    stop_price: Decimal | None = None
    trail_price: Decimal | None = None
    trail_percent: Decimal | None = None
    # NEW (P3a) — bracket / OTO / OCO carrier (the entry/anchor ticket carries
    # its protective exits): "simple" | "bracket" | "oto" | "oco".
    order_class: str = "simple"
    take_profit_limit_price: Decimal | None = None
    stop_loss_stop_price: Decimal | None = None
    stop_loss_limit_price: Decimal | None = None   # present ⇒ stop-limit exit


@dataclass(frozen=True)
class OrderSnapshot:
    broker_order_id: str
    client_order_id: str
    ticker: str
    side: str
    quantity: Decimal
    order_type: str
    limit_price: Decimal | None
    time_in_force: str
    status: str                # draft|submitted|partial|filled|cancelled|rejected|error
    filled_quantity: Decimal = Decimal("0")
    avg_fill_price: Decimal | None = None
    queued_until_open: bool = False
    # NEW (P3a) — echoed back for stop / stop_limit / trailing + grouped orders:
    stop_price: Decimal | None = None
    trail_price: Decimal | None = None
    trail_percent: Decimal | None = None
    order_class: str = "simple"
    leg_role: str = ""         # "" | "entry" | "stop_loss" | "take_profit"
    legs: list[OrderSnapshot] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class BrokerError(Exception):
    """Adapter-side, user-visible error (rejection, validation failure)."""


class BrokerAuthError(BrokerError):
    """Credentials rejected by the broker (HTTP 401/403). A *permanent*
    failure — the account needs re-authentication, not a retry. Callers in
    the poll loop flip the account to ``needs_reauth`` instead of erroring
    (and log-spamming) every cycle. Subclasses ``BrokerError`` so existing
    ``except BrokerError`` handlers still degrade gracefully."""


class BrokerTransientError(Exception):
    """Network/timeout — caller treats the order outcome as unknown."""


class Broker(Protocol):
    capabilities: BrokerCapabilities

    def get_account(self) -> AccountSnapshot: ...
    def get_positions(self) -> list[PositionSnapshot]: ...
    def get_recent_fills(self, since: datetime) -> list[FillSnapshot]: ...
    def submit_order(self, ticket: OrderTicket) -> OrderSnapshot: ...
    def cancel_order(self, broker_order_id: str) -> None: ...
    def get_order(self, broker_order_id: str) -> OrderSnapshot: ...
    # Grouped paths (P3a). Brokers without bracket support
    # (supports_bracket=False) need not implement these.
    def submit_bracket(self, ticket: OrderTicket) -> OrderSnapshot: ...
    def submit_protective(self, ticket: OrderTicket) -> OrderSnapshot: ...
    def find_order_by_client_id(
        self,
        client_order_id: str,
        *,
        order_meta: OrderMeta | None = None,
    ) -> OrderSnapshot | None: ...


@dataclass(frozen=True)
class OrderMeta:
    """Metadata passed to `find_order_by_client_id` to support adapters
    (TradeStation) that have no broker-side client id and must heuristic-
    scan recent orders by ticker/side/quantity/created_at. Adapters with a
    real broker-side client id (IBKR, Alpaca) ignore this — see ADR 0012.
    """

    ticker: str
    side: str            # "buy" | "sell"
    quantity: Decimal
    created_at: datetime
