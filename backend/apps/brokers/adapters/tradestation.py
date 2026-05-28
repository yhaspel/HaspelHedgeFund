"""TradeStationBroker — TradeStation Web API (/v3) adapter (P3a-3).

See `phase-03a-3-paper-trading-tradestation.md` and ADR 0012.

- Pure adapter: every method returns framework DTOs (interfaces.py). No
  raw TradeStation JSON leaks out.
- OAuth 2.0 authorization-code flow with refresh tokens; PKCE-protected
  exchange handled in `tradestation_oauth.py`.
- Environment routing: the chosen API base URL is stored on
  `BrokerAccount.config["api_base_url"]` so a paper account is
  structurally incapable of reaching the production host. The adapter
  reads this field, never the global setting, on every request.
- `supports_live=False` — live execution is gated on P3a-6 (native
  brackets). The live code path is built and tested here.
- Exception contract:
    * `BrokerTransientError` for network / timeout / 5xx — submit
      parks the order in `idempotency_state="unknown"`.
    * `BrokerError` for definite broker-side refusals (validation,
      4xx).
- `find_order_by_client_id` uses a heuristic scan because TradeStation
  does not echo a client id. Match keys: ticker (case-insensitive),
  side, quantity (exact), and a ±10-minute timestamp window.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import requests
from django.conf import settings

from ..capabilities import (
    AUTH_OAUTH2,
    BrokerCapabilities,
    register_broker,
)
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
from .tradestation_oauth import refresh_if_needed

log = logging.getLogger(__name__)


# --- Capability descriptor --------------------------------------------------

CAPABILITIES = BrokerCapabilities(
    code="tradestation",
    display_name="TradeStation (paper / live)",
    auth_kind=AUTH_OAUTH2,
    supports_paper=True,
    supports_live=False,  # ADR 0012 §3; flipped True in P3a-6 brackets.
    supports_fractional=False,
    quantity_increment=Decimal("1"),
    supported_order_types=("market", "limit"),
    supported_time_in_force=("day", "gtc"),
    description=(
        "TradeStation Web API via OAuth 2.0. Paper uses the SIM "
        "environment; live is gated on bracket orders (P3a-6)."
    ),
    available=True,
    connect_form=(),
)


# --- TradeStation ↔ framework status mapping --------------------------------
#
# TradeStation order Status values (observed from /v3/brokerage/accounts/
# {id}/orders): Received, Sent, Open, PartiallyFilled, Filled, Cancelled,
# Rejected, Expired, Replaced, BrokerCancel, OutByCalc, OpenOpening,
# OpenClosing. Group these onto the framework's {submitted | partial |
# filled | cancelled | rejected | error}. An unrecognised status maps to
# `error` — never silently to `filled`.

_STATUS_MAP: dict[str, str] = {
    "received": "submitted",
    "sent": "submitted",
    "open": "submitted",
    "openopening": "submitted",
    "openclosing": "submitted",
    "queued": "submitted",
    "partiallyfilled": "partial",
    "filled": "filled",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "brokercancel": "cancelled",
    "expired": "cancelled",
    "replaced": "cancelled",
    "outbycalc": "cancelled",
    "rejected": "rejected",
}


def _map_status(ts_status: str) -> str:
    return _STATUS_MAP.get((ts_status or "").strip().lower(), "error")


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


def _parse_ts_time(raw: Any) -> datetime:
    """TradeStation timestamps are ISO-8601 UTC (e.g. '2026-05-28T13:45:00Z')."""
    if isinstance(raw, str) and raw:
        s = raw.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return datetime.now(tz=UTC)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    return datetime.now(tz=UTC)


# --- API base URL routing ---------------------------------------------------


def api_base_for_mode(mode: str) -> str:
    """The canonical base URL for `mode`. Stored on
    `BrokerAccount.config["api_base_url"]` at activation time so the
    adapter never reaches for the wrong host at runtime."""
    if mode == BrokerAccount.MODE_LIVE:
        return settings.TRADESTATION_API_BASE_LIVE
    return settings.TRADESTATION_API_BASE_SIM


def assert_paper_host_safety(account: BrokerAccount) -> None:
    """Defensive: a paper account is structurally forbidden from holding
    the production host. Raises BrokerError if its config says otherwise.
    Called on every adapter construction so a mutated config can't bleed
    into a request."""
    if account.mode != BrokerAccount.MODE_PAPER:
        return
    base = (account.config or {}).get("api_base_url") or ""
    live = settings.TRADESTATION_API_BASE_LIVE
    if base and base == live:
        raise BrokerError(
            f"BrokerAccount {account.pk} is paper but its api_base_url is "
            "configured for the live host — refusing.",
        )


# --- HTTP transport ---------------------------------------------------------


class _TSSession:
    """Per-adapter HTTP session bound to the chosen base URL and a fresh
    access token. Refreshes the token on construction and on 401."""

    def __init__(self, account: BrokerAccount) -> None:
        self._account = account
        self._base = (
            (account.config or {}).get("api_base_url")
            or api_base_for_mode(account.mode)
        ).rstrip("/")
        self._token = refresh_if_needed(account)
        self._session = requests.Session()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        _retried: bool = False,
    ) -> Any:
        url = f"{self._base}{path}"
        try:
            r = self._session.request(
                method, url, params=params, json=json,
                headers=self._headers(), timeout=20,
            )
        except requests.RequestException as exc:
            raise BrokerTransientError(
                f"TradeStation {method} {path} failed: {exc}",
            ) from exc
        if r.status_code == 401 and not _retried:
            # Likely the token expired between calls. Refresh once and retry.
            self._token = refresh_if_needed(self._account)
            return self.request(
                method, path, params=params, json=json, _retried=True,
            )
        if r.status_code >= 500:
            raise BrokerTransientError(
                f"TradeStation {method} {path} → {r.status_code}: "
                f"{r.text[:200]}",
            )
        if r.status_code >= 400:
            raise BrokerError(
                f"TradeStation {method} {path} → {r.status_code}: "
                f"{r.text[:200]}",
            )
        if not r.content:
            return None
        try:
            return r.json()
        except ValueError as exc:
            raise BrokerError(
                f"TradeStation {method} {path} response not JSON: "
                f"{r.text[:200]}",
            ) from exc

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)


# --- TradeAction derivation (Risks #8) --------------------------------------


def _trade_action(
    side: str, current_quantity: Decimal,
) -> str:
    """TradeStation requires BUY / SELL / SELLSHORT / BUYTOCOVER. The
    framework `side` is only buy/sell — consult the live position to pick
    short-entry vs long-exit. `current_quantity` is the signed position
    (positive = long, negative = short, zero = flat)."""
    if side == "buy":
        # Closing a short, or opening/adding to a long.
        return "BUYTOCOVER" if current_quantity < 0 else "BUY"
    # side == "sell"
    if current_quantity > 0:
        return "SELL"  # long exit
    return "SELLSHORT"  # flat → short, or adding to short


# --- The adapter ------------------------------------------------------------


class TradeStationBroker:
    capabilities = CAPABILITIES

    # Heuristic scan window for find_order_by_client_id.
    SCAN_WINDOW = timedelta(minutes=10)

    def __init__(self, account: BrokerAccount) -> None:
        assert_paper_host_safety(account)
        self._account = account
        self._account_id = account.account_id
        self._session = _TSSession(account)

    # -- get_account ------------------------------------------------------

    def get_account(self) -> AccountSnapshot:
        raw = self._session.get(
            f"/brokerage/accounts/{self._account_id}/balances",
        ) or {}
        # /balances returns {"Balances":[{...}], "Errors":[...]}.
        rows = raw.get("Balances") if isinstance(raw, dict) else None
        row = (rows or [{}])[0] if rows else (raw if isinstance(raw, dict) else {})
        cash = _dec(row.get("CashBalance"))
        bp = _dec(row.get("BuyingPower")) or cash
        equity = _dec(row.get("Equity")) or cash
        currency = row.get("Currency") or self._account.base_currency or "USD"
        return AccountSnapshot(
            account_id=self._account_id,
            cash=cash,
            buying_power=bp,
            equity=equity,
            currency=currency,
            raw=raw,
        )

    # -- get_positions ----------------------------------------------------

    def get_positions(self) -> list[PositionSnapshot]:
        raw = self._session.get(
            f"/brokerage/accounts/{self._account_id}/positions",
        ) or {}
        rows = raw.get("Positions") if isinstance(raw, dict) else raw
        out: list[PositionSnapshot] = []
        for row in rows or []:
            ticker = (row.get("Symbol") or "").upper()
            if not ticker:
                continue
            qty = _dec(row.get("Quantity"))
            if qty == 0:
                continue
            # TradeStation reports unsigned quantity + a LongShort flag.
            if (row.get("LongShort") or "").lower() == "short":
                qty = -qty
            out.append(
                PositionSnapshot(
                    ticker=ticker,
                    quantity=qty,
                    avg_cost=_dec(row.get("AveragePrice")),
                    raw=row,
                ),
            )
        return out

    # -- get_recent_fills -------------------------------------------------

    def get_recent_fills(self, since: datetime) -> list[FillSnapshot]:
        """Pull executions from /orders (today) and /historicalorders
        (when `since` predates today). Filter client-side by `since`."""
        rows = self._fetch_orders_with_executions(since=since)
        fills: list[FillSnapshot] = []
        for raw in rows:
            for leg in raw.get("Legs") or []:
                for ex in leg.get("ExecutionInfo") or raw.get("Executions") or []:
                    filled_at = _parse_ts_time(
                        ex.get("ExecutionTime") or ex.get("Time"),
                    )
                    if filled_at < since:
                        continue
                    side_raw = (leg.get("BuyOrSell") or "").lower()
                    side = "sell" if side_raw.startswith("sell") else "buy"
                    fills.append(
                        FillSnapshot(
                            broker_fill_id=str(
                                ex.get("ExecutionID")
                                or f"{raw.get('OrderID')}-{ex.get('ExecutionTime')}",
                            ),
                            broker_order_id=str(raw.get("OrderID") or ""),
                            ticker=(leg.get("Symbol") or "").upper(),
                            quantity=_dec(ex.get("Quantity") or leg.get("ExecQuantity")),
                            price=_dec(ex.get("Price") or leg.get("ExecutionPrice")),
                            filled_at=filled_at,
                            side=side,
                            raw={"order": raw, "execution": ex},
                        ),
                    )
        return fills

    def _fetch_orders_with_executions(self, *, since: datetime) -> list[dict]:
        today_utc = datetime.now(tz=UTC).date()
        out: list[dict] = []
        try:
            current = self._session.get(
                f"/brokerage/accounts/{self._account_id}/orders",
            ) or {}
        except BrokerError:
            current = {}
        out.extend(current.get("Orders") or [])
        if since.date() < today_utc:
            try:
                hist = self._session.get(
                    f"/brokerage/accounts/{self._account_id}/historicalorders",
                    params={"since": since.date().isoformat()},
                ) or {}
            except BrokerError:
                hist = {}
            out.extend(hist.get("Orders") or [])
        return out

    # -- submit_order -----------------------------------------------------

    def submit_order(self, ticket: OrderTicket) -> OrderSnapshot:
        # Position lookup for TradeAction selection (Risks #8). Fail closed
        # on lookup failure — better to bounce a submit than to guess.
        positions = self.get_positions()
        current = next(
            (p.quantity for p in positions if p.ticker == ticket.ticker.upper()),
            Decimal("0"),
        )
        action = _trade_action(ticket.side, current)
        body = {
            "AccountID": self._account_id,
            "Symbol": ticket.ticker.upper(),
            "Quantity": str(ticket.quantity),
            "OrderType": "Market" if ticket.order_type == "market" else "Limit",
            "TradeAction": action,
            "TimeInForce": {"Duration": (ticket.time_in_force or "day").upper()},
            "Route": "Intelligent",
        }
        if ticket.order_type == "limit" and ticket.limit_price is not None:
            body["LimitPrice"] = str(ticket.limit_price)
        resp = self._session.post("/orderexecution/orders", json=body) or {}
        # TradeStation returns {"Orders":[{...}], "Errors":[...]}.
        errors = resp.get("Errors") or []
        if errors:
            messages = [
                f"{e.get('OrderID', '?')}: {e.get('Error') or e.get('Message')}"
                for e in errors
            ]
            raise BrokerError(
                "TradeStation rejected the order: " + "; ".join(messages),
            )
        orders = resp.get("Orders") or []
        if not orders:
            raise BrokerError(
                f"TradeStation returned no Orders block: {resp!r}",
            )
        accepted = orders[0]
        return OrderSnapshot(
            broker_order_id=str(accepted.get("OrderID") or ""),
            client_order_id=ticket.client_order_id,
            ticker=ticket.ticker.upper(),
            side=ticket.side,
            quantity=ticket.quantity,
            order_type=ticket.order_type,
            limit_price=ticket.limit_price,
            time_in_force=ticket.time_in_force or "day",
            status="submitted",
            filled_quantity=Decimal("0"),
            avg_fill_price=None,
            raw=accepted,
        )

    # -- get_order --------------------------------------------------------

    def get_order(self, broker_order_id: str) -> OrderSnapshot:
        raw = self._session.get(
            f"/brokerage/accounts/{self._account_id}/orders/{broker_order_id}",
        ) or {}
        orders = raw.get("Orders") if isinstance(raw, dict) else None
        if orders:
            return self._raw_order_to_snapshot(orders[0])
        if isinstance(raw, dict) and raw.get("OrderID"):
            return self._raw_order_to_snapshot(raw)
        raise BrokerError(f"TradeStation order {broker_order_id} not found")

    # -- cancel_order -----------------------------------------------------

    def cancel_order(self, broker_order_id: str) -> None:
        self._session.delete(f"/orderexecution/orders/{broker_order_id}")

    # -- find_order_by_client_id -----------------------------------------

    def find_order_by_client_id(
        self,
        client_order_id: str,
        *,
        order_meta: OrderMeta | None = None,
    ) -> OrderSnapshot | None:
        """Heuristic scan. TradeStation does not echo a client id, so we
        scan today's orders (and yesterday's via historicalorders if the
        order is old) and match on ticker + side + quantity within a
        ±SCAN_WINDOW of created_at. Returns the first match or None."""
        if order_meta is None:
            return None  # cannot scan without the metadata
        since = order_meta.created_at - self.SCAN_WINDOW
        rows = self._fetch_orders_with_executions(since=since)
        for raw in rows:
            snap = self._raw_order_to_snapshot(raw)
            if not _matches_meta(snap, order_meta, self.SCAN_WINDOW):
                continue
            # Stamp the client id we believe this to be (the local row).
            return OrderSnapshot(
                broker_order_id=snap.broker_order_id,
                client_order_id=client_order_id,
                ticker=snap.ticker,
                side=snap.side,
                quantity=snap.quantity,
                order_type=snap.order_type,
                limit_price=snap.limit_price,
                time_in_force=snap.time_in_force,
                status=snap.status,
                filled_quantity=snap.filled_quantity,
                avg_fill_price=snap.avg_fill_price,
                raw=snap.raw,
            )
        return None

    # -- raw → DTO --------------------------------------------------------

    def _raw_order_to_snapshot(self, raw: dict) -> OrderSnapshot:
        legs = raw.get("Legs") or [{}]
        leg = legs[0]
        side_raw = (leg.get("BuyOrSell") or raw.get("TradeAction") or "").lower()
        side = "sell" if "sell" in side_raw else "buy"
        order_type_raw = (raw.get("OrderType") or "").lower()
        order_type = "limit" if "limit" in order_type_raw else "market"
        total_qty = _dec(leg.get("QuantityOrdered") or raw.get("Quantity"))
        filled_qty = _dec(leg.get("ExecQuantity") or raw.get("FilledQuantity"))
        status = _map_status(raw.get("Status") or raw.get("StatusDescription") or "")
        return OrderSnapshot(
            broker_order_id=str(raw.get("OrderID") or ""),
            client_order_id="",
            ticker=(leg.get("Symbol") or raw.get("Symbol") or "").upper(),
            side=side,
            quantity=total_qty,
            order_type=order_type,
            limit_price=_opt_dec(raw.get("LimitPrice")),
            time_in_force=str(
                (raw.get("TimeInForce") or {}).get("Duration")
                or raw.get("Duration") or "day",
            ).lower(),
            status=status,
            filled_quantity=filled_qty,
            avg_fill_price=_opt_dec(
                leg.get("ExecutionPrice") or raw.get("FilledPrice"),
            ),
            raw=raw,
        )


def _matches_meta(
    snap: OrderSnapshot, meta: OrderMeta, window: timedelta,
) -> bool:
    if snap.ticker.upper() != meta.ticker.upper():
        return False
    if snap.side != meta.side:
        return False
    if snap.quantity != meta.quantity:
        return False
    raw_time = (
        snap.raw.get("OpenedDateTime")
        or snap.raw.get("CreatedAt")
        or snap.raw.get("ExecutionTime")
    )
    if raw_time:
        t = _parse_ts_time(raw_time)
        delta = abs(t - meta.created_at)
        if delta > window:
            return False
    return True


# --- Registration -----------------------------------------------------------


def _factory(account: BrokerAccount) -> TradeStationBroker:
    return TradeStationBroker(account)


register_broker(CAPABILITIES, _factory)
