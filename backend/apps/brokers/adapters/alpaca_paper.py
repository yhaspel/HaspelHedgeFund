"""AlpacaPaperBroker — Alpaca **paper** Trading API adapter (P3a-4).

See `phase-03a-4-paper-trading-alpaca.md` and ADR 0013.

Paper-only — by construction, not by convention:

- `supports_live=False` on the capability descriptor (the scaffolding's
  `BrokerAccount.mode` validator rejects `mode="live"` for any broker
  whose `supports_live` is False).
- The adapter constructs `TradingClient(..., paper=True)` and never
  passes `url_override`. The Alpaca **live host string** never appears
  in this module — a CI test greps for it to enforce that.
- `assert_paper(account)` re-checks the mode on every adapter
  construction so a mutated account row can't bleed into a live call.

`client_order_id` idempotency is native — Alpaca rejects a second
`POST /v2/orders` with the same `client_order_id` with HTTP 422. The
adapter catches that specific failure and resolves the pre-existing
order via `get_order_by_client_id`, so a retried submission cannot
create a duplicate Alpaca order.

Fills are pulled via `get_orders(status="closed", after=since)`. The
plan considered `account/activities/FILL` as an alternative; ADR 0013
documents the choice (orders endpoint is what the SDK exposes
first-class, partial fills are still visible via `filled_qty` and
`filled_avg_price` on each order row).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ..capabilities import (
    AUTH_API_KEY,
    BrokerCapabilities,
    register_broker,
)
from ..credentials import with_credential
from ..interfaces import (
    AccountSnapshot,
    BrokerError,
    BrokerTransientError,
    FillSnapshot,
    OrderMeta,
    OrderSnapshot,
    OrderTicket,
    PositionSnapshot,
)
from ..models import BrokerAccount

log = logging.getLogger(__name__)


# --- Capability descriptor --------------------------------------------------

CAPABILITIES = BrokerCapabilities(
    code="alpaca_paper",
    display_name="Alpaca (paper only)",
    auth_kind=AUTH_API_KEY,
    supports_paper=True,
    supports_live=False,  # structural — see module docstring + ADR 0013.
    supports_fractional=True,
    quantity_increment=Decimal("0.000001"),
    supported_order_types=("market", "limit"),
    supported_time_in_force=("day", "gtc"),
    description=(
        "Alpaca paper Trading API. API key id + secret. Verified end-to-end "
        "against a live Alpaca paper sandbox 2026-05-29."
    ),
    available=True,
    community_unverified=False,
    connect_form=(
        {"name": "api_key", "label": "API key ID", "kind": "text"},
        {"name": "api_secret", "label": "Secret key", "kind": "password"},
    ),
)


# --- Alpaca ↔ framework status mapping --------------------------------------
#
# Alpaca `OrderStatus` values (from alpaca.trading.enums.OrderStatus) are
# mapped onto the framework's {submitted | partial | filled | cancelled |
# rejected | error}. Anything unrecognised maps to `error`, never silently
# to `filled`.

_STATUS_MAP: dict[str, str] = {
    "new": "submitted",
    "accepted": "submitted",
    "pending_new": "submitted",
    "accepted_for_bidding": "submitted",
    "pending_review": "submitted",
    "held": "submitted",
    "suspended": "submitted",
    "calculated": "submitted",
    "stopped": "submitted",
    "partially_filled": "partial",
    "filled": "filled",
    "done_for_day": "cancelled",
    "canceled": "cancelled",
    "cancelled": "cancelled",
    "expired": "cancelled",
    "replaced": "cancelled",
    "pending_cancel": "cancelled",
    "pending_replace": "cancelled",
    "rejected": "rejected",
}


def _map_status(raw: Any) -> str:
    return _STATUS_MAP.get(_enum_value(raw).lower(), "error")


def _enum_value(v: Any) -> str:
    if v is None:
        return ""
    if hasattr(v, "value"):
        return str(v.value)
    return str(v)


def _dec(v: Any, default: Decimal = Decimal("0")) -> Decimal:
    if v in (None, ""):
        return default
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return default


def _opt_dec(v: Any) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return None


def _to_dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    if isinstance(v, str) and v:
        s = v.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return datetime.now(tz=UTC)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return datetime.now(tz=UTC)


def assert_paper(account: BrokerAccount) -> None:
    """Defensive: refuse to construct the adapter against a non-paper
    `BrokerAccount`. The capability descriptor already forbids `mode=live`
    at account-creation time; this is the runtime backstop."""
    if account.mode != BrokerAccount.MODE_PAPER:
        raise BrokerError(
            f"BrokerAccount {account.pk} is not paper — Alpaca is paper-only.",
        )


# --- Rate limiter -----------------------------------------------------------
#
# Alpaca paper allows ~200 req/min. A sliding-window limiter keeps the
# adapter inside that budget; the polling cadences from the scaffolding
# (positions cached ~10 s, `poll_open_orders` every 30 s, `reconcile_account`
# every 5 min) are well under it for a single account but the limiter is
# still required so a manual sync burst can't trip the wire.


class _RateLimiter:
    def __init__(self, max_calls: int = 180, window_s: float = 60.0) -> None:
        self.max_calls = max_calls
        self.window_s = window_s
        self._stamps: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            while self._stamps and now - self._stamps[0] > self.window_s:
                self._stamps.popleft()
            if len(self._stamps) >= self.max_calls:
                sleep_for = self.window_s - (now - self._stamps[0]) + 0.01
                if sleep_for > 0:
                    time.sleep(sleep_for)
                now = time.monotonic()
                while self._stamps and now - self._stamps[0] > self.window_s:
                    self._stamps.popleft()
            self._stamps.append(now)


_LIMITER = _RateLimiter(max_calls=180, window_s=60.0)


# --- SDK client construction -------------------------------------------------


def _make_client(api_key: str, api_secret: str):
    """Construct an Alpaca `TradingClient` bound to the paper host.

    Imported lazily so test code can patch `_make_client` without paying
    the SDK import cost during collection.
    """
    from alpaca.trading.client import TradingClient

    # paper=True is the only path. url_override is never set — that
    # parameter could be used to point at the live host, so we don't
    # expose it through any code path. ADR 0013 §1.
    return TradingClient(
        api_key=api_key, secret_key=api_secret, paper=True,
    )


# --- Exception translation --------------------------------------------------


def _raise_translated(exc: Exception, op: str) -> None:
    """Translate Alpaca SDK errors into framework exceptions."""
    # Lazy import so the SDK isn't loaded at module import time.
    from alpaca.common.exceptions import APIError

    if isinstance(exc, APIError):
        status_code = getattr(exc, "status_code", None) or 0
        if status_code >= 500:
            raise BrokerTransientError(f"Alpaca {op} → {status_code}: {exc}") from exc
        raise BrokerError(f"Alpaca {op} → {status_code}: {exc}") from exc
    raise BrokerTransientError(f"Alpaca {op} failed: {exc}") from exc


def _is_duplicate_client_order_id(exc: Exception) -> bool:
    """Alpaca rejects a second POST /v2/orders with the same
    client_order_id with HTTP 422 and a message like 'client_order_id
    must be unique'. Match defensively on both signals."""
    from alpaca.common.exceptions import APIError

    if not isinstance(exc, APIError):
        return False
    if getattr(exc, "status_code", None) != 422:
        return False
    msg = str(getattr(exc, "message", None) or exc).lower()
    return "client_order_id" in msg and (
        "unique" in msg or "exists" in msg or "duplicate" in msg
    )


# --- The adapter ------------------------------------------------------------


class AlpacaPaperBroker:
    capabilities = CAPABILITIES

    def __init__(self, account: BrokerAccount) -> None:
        assert_paper(account)
        self._account = account
        self._client = _resolve_client(account)

    # -- get_account ------------------------------------------------------

    def get_account(self) -> AccountSnapshot:
        _LIMITER.acquire()
        try:
            row = self._client.get_account()
        except Exception as exc:  # noqa: BLE001
            _raise_translated(exc, "get_account")
        cash = _dec(getattr(row, "cash", None))
        bp = _dec(getattr(row, "buying_power", None)) or cash
        equity = _dec(getattr(row, "equity", None)) or cash
        currency = getattr(row, "currency", None) or self._account.base_currency or "USD"
        return AccountSnapshot(
            account_id=str(getattr(row, "account_number", None)
                           or getattr(row, "id", "")
                           or self._account.account_id),
            cash=cash,
            buying_power=bp,
            equity=equity,
            currency=str(currency),
            raw=_to_raw(row),
        )

    # -- get_positions ----------------------------------------------------

    def get_positions(self) -> list[PositionSnapshot]:
        _LIMITER.acquire()
        try:
            rows = self._client.get_all_positions() or []
        except Exception as exc:  # noqa: BLE001
            _raise_translated(exc, "get_all_positions")
        out: list[PositionSnapshot] = []
        for row in rows:
            ticker = (getattr(row, "symbol", "") or "").upper()
            if not ticker:
                continue
            qty = _dec(getattr(row, "qty", None))
            if qty == 0:
                continue
            side = _enum_value(getattr(row, "side", "")).lower()
            if side == "short" and qty > 0:
                qty = -qty
            out.append(
                PositionSnapshot(
                    ticker=ticker,
                    quantity=qty,
                    avg_cost=_dec(getattr(row, "avg_entry_price", None)),
                    raw=_to_raw(row),
                ),
            )
        return out

    # -- get_recent_fills -------------------------------------------------
    #
    # ADR 0013 §2: closed orders are the primary fills source. Each
    # closed order carries `filled_qty` + `filled_avg_price` + `filled_at`
    # so partial fills land as their own row when the residual stays open
    # (the open half shows up on the next poll). The /activities/FILL
    # endpoint would offer per-execution granularity but isn't first-class
    # in the SDK; we accept the simpler source for v1.

    def get_recent_fills(self, since: datetime) -> list[FillSnapshot]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        _LIMITER.acquire()
        req = GetOrdersRequest(
            status=QueryOrderStatus.CLOSED, after=since, limit=500,
        )
        try:
            rows = self._client.get_orders(filter=req) or []
        except Exception as exc:  # noqa: BLE001
            _raise_translated(exc, "get_orders(closed)")
        fills: list[FillSnapshot] = []
        for row in rows:
            filled_qty = _dec(getattr(row, "filled_qty", None))
            if filled_qty <= 0:
                continue
            filled_at = _to_dt(
                getattr(row, "filled_at", None) or getattr(row, "updated_at", None),
            )
            if filled_at < since:
                continue
            side = _enum_value(getattr(row, "side", "")).lower() or "buy"
            order_id = str(getattr(row, "id", "") or "")
            fills.append(
                FillSnapshot(
                    broker_fill_id=f"{order_id}-{int(filled_at.timestamp())}",
                    broker_order_id=order_id,
                    ticker=(getattr(row, "symbol", "") or "").upper(),
                    quantity=filled_qty,
                    price=_dec(getattr(row, "filled_avg_price", None)),
                    filled_at=filled_at,
                    side="sell" if side.startswith("sell") else "buy",
                    raw=_to_raw(row),
                ),
            )
        return fills

    # -- submit_order -----------------------------------------------------

    def submit_order(self, ticket: OrderTicket) -> OrderSnapshot:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

        side = OrderSide.BUY if ticket.side == "buy" else OrderSide.SELL
        tif = TimeInForce.GTC if (ticket.time_in_force or "day") == "gtc" else TimeInForce.DAY
        common = dict(
            symbol=ticket.ticker.upper(),
            qty=float(ticket.quantity),
            side=side,
            time_in_force=tif,
            client_order_id=ticket.client_order_id,
        )
        if ticket.order_type == "limit":
            if ticket.limit_price is None:
                raise BrokerError("limit order requires limit_price")
            req = LimitOrderRequest(limit_price=float(ticket.limit_price), **common)
        else:
            req = MarketOrderRequest(**common)

        _LIMITER.acquire()
        try:
            order = self._client.submit_order(order_data=req)
        except Exception as exc:  # noqa: BLE001
            # Native idempotency: a retried POST with the same
            # client_order_id returns 422. Treat that as proof the first
            # order already exists, resolve it, and surface the existing
            # snapshot instead of failing.
            if _is_duplicate_client_order_id(exc):
                existing = self.find_order_by_client_id(ticket.client_order_id)
                if existing is not None:
                    return existing
            _raise_translated(exc, "submit_order")
        return self._snapshot_from_order(order, fallback_ticket=ticket)

    # -- get_order --------------------------------------------------------

    def get_order(self, broker_order_id: str) -> OrderSnapshot:
        _LIMITER.acquire()
        try:
            order = self._client.get_order_by_id(order_id=broker_order_id)
        except Exception as exc:  # noqa: BLE001
            _raise_translated(exc, f"get_order_by_id({broker_order_id})")
        return self._snapshot_from_order(order)

    # -- cancel_order -----------------------------------------------------

    def cancel_order(self, broker_order_id: str) -> None:
        _LIMITER.acquire()
        try:
            self._client.cancel_order_by_id(order_id=broker_order_id)
        except Exception as exc:  # noqa: BLE001
            _raise_translated(exc, f"cancel_order_by_id({broker_order_id})")

    # -- find_order_by_client_id -----------------------------------------

    def find_order_by_client_id(
        self,
        client_order_id: str,
        *,
        order_meta: OrderMeta | None = None,
    ) -> OrderSnapshot | None:
        """Native lookup: Alpaca echoes our `client_order_id` and exposes
        `GET /v2/orders:by_client_order_id`. `order_meta` is ignored —
        Alpaca has a real broker-side client id (see ADR 0012)."""
        _LIMITER.acquire()
        try:
            # SDK uses positional `client_id` arg (not `client_order_id`),
            # despite the corresponding submit_order field being named
            # `client_order_id`. Verified against alpaca-py 0.43.4 via the
            # live-sandbox checklist.
            order = self._client.get_order_by_client_id(client_order_id)
        except Exception as exc:  # noqa: BLE001
            from alpaca.common.exceptions import APIError

            if isinstance(exc, APIError) and getattr(exc, "status_code", None) == 404:
                return None
            _raise_translated(exc, f"get_order_by_client_id({client_order_id})")
        if order is None:
            return None
        return self._snapshot_from_order(order)

    # -- SDK row → DTO ----------------------------------------------------

    def _snapshot_from_order(
        self, order: Any, *, fallback_ticket: OrderTicket | None = None,
    ) -> OrderSnapshot:
        order_type_raw = _enum_value(getattr(order, "order_type", None)
                                     or getattr(order, "type", None)).lower()
        order_type = "limit" if "limit" in order_type_raw else "market"
        side_raw = _enum_value(getattr(order, "side", "")).lower()
        side = "sell" if side_raw.startswith("sell") else "buy"
        tif_raw = _enum_value(getattr(order, "time_in_force", "")).lower() or "day"
        quantity = _dec(
            getattr(order, "qty", None)
            or (fallback_ticket.quantity if fallback_ticket else None),
        )
        status = _map_status(getattr(order, "status", None))
        return OrderSnapshot(
            broker_order_id=str(getattr(order, "id", "") or ""),
            client_order_id=str(
                getattr(order, "client_order_id", None)
                or (fallback_ticket.client_order_id if fallback_ticket else "")
                or "",
            ),
            ticker=(getattr(order, "symbol", "") or "").upper(),
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=_opt_dec(getattr(order, "limit_price", None)),
            time_in_force=tif_raw,
            status=status,
            filled_quantity=_dec(getattr(order, "filled_qty", None)),
            avg_fill_price=_opt_dec(getattr(order, "filled_avg_price", None)),
            raw=_to_raw(order),
        )


# --- Helpers ----------------------------------------------------------------


def _to_raw(obj: Any) -> dict:
    if obj is None:
        return {}
    for attr in ("model_dump", "dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return {k: _json_safe(v) for k, v in fn().items()}
            except Exception:  # noqa: BLE001
                pass
    return {}


def _json_safe(v: Any) -> Any:
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if hasattr(v, "value"):
        return v.value
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _json_safe(val) for k, val in v.items()}
    return str(v)


def _resolve_client(account: BrokerAccount):
    """Pull the api_key/secret out of the encrypted credential store and
    construct a paper-only TradingClient. Held only for the lifetime of
    the adapter instance."""
    def _build(secrets: dict):
        return _make_client(
            api_key=secrets.get("api_key", ""),
            api_secret=secrets.get("api_secret", ""),
        )
    return with_credential(account, _build)


# --- Registration -----------------------------------------------------------


def _factory(account: BrokerAccount) -> AlpacaPaperBroker:
    return AlpacaPaperBroker(account)


register_broker(CAPABILITIES, _factory)
