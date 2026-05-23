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
    order_type: str = "market"  # "market" | "limit"
    limit_price: Decimal | None = None
    time_in_force: str = "day"  # "day" | "gtc"


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
    raw: dict[str, Any] = field(default_factory=dict)


class BrokerError(Exception):
    """Adapter-side, user-visible error (rejection, validation failure)."""


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
    def find_order_by_client_id(
        self, client_order_id: str,
    ) -> OrderSnapshot | None: ...
