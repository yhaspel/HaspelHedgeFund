"""IBKRBroker — Interactive Brokers adapter via the Client Portal Gateway.

P3a-2. See `phase-03a-2-paper-trading-ibkr.md` and ADR 0011 for the design
record. Highlights:

- Pure adapter: every method returns framework DTOs (interfaces.py). No
  raw IBKR JSON leaks out.
- HTTP transport lives in `ibkr_gateway.py`. This module owns the
  endpoint mapping, DTO translation, conid resolution, status mapping,
  the reply-loop *policy* (allowlist + iteration cap — ADR 0011 §3), and
  the duplicate-`cOID` resolution path (Risks #7c).
- Exception contract:
    * `BrokerTransientError` for connection / timeout / 5xx — `submit_idempotent`
      parks the order in `idempotency_state="unknown"`; `find_order_by_client_id`
      adopts it on reconcile.
    * `BrokerError` for definite broker-side refusals (validation errors,
      blocking reply-loop messages, generic 4xx).
- Capability: `supports_live=False` — flipped to `True` in P3a-6 once the
  IBKR native-bracket section ships (live-execution gate). Replaces the
  3a-1 placeholder in `_placeholder_capabilities`.

Reply-loop allowlist (`REPLY_ALLOWLIST` below) ships **empty** in v1.
Every IBKR confirmation message routes to `status="rejected"` with the
verbatim message preserved in `raw["reply_messages"]`. The manual
paper-account run populates the allowlist — see Risks #7 in the phase
plan.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from django.utils import timezone

from ..capabilities import (
    AUTH_GATEWAY,
    BrokerCapabilities,
    register_broker,
)
from ..interfaces import (
    AccountSnapshot,
    BrokerError,
    FillSnapshot,
    OrderSnapshot,
    OrderTicket,
    PositionSnapshot,
)
from ..models import BrokerAccount
from .ibkr_gateway import IBKRGatewaySession

log = logging.getLogger(__name__)


# --- Capability descriptor --------------------------------------------------

CAPABILITIES = BrokerCapabilities(
    code="ibkr",
    display_name="Interactive Brokers",
    auth_kind=AUTH_GATEWAY,
    supports_paper=True,
    supports_live=False,  # ADR 0011 §1; flipped True in P3a-6 native brackets
    supports_fractional=True,
    quantity_increment=Decimal("0.0001"),
    supported_order_types=("market", "limit"),
    supported_time_in_force=("day", "gtc"),
    description=(
        "Interactive Brokers via headless Client Portal Gateway. "
        "Paper-only until P3a-6 ships native brackets."
    ),
    available=True,
    connect_form=(),  # gateway URLs are Django settings, not per-account
)


# --- Reply-loop policy (ADR 0011 §3) ----------------------------------------
#
# v1 SHIPS EMPTY. Every reply-loop message routes to status="rejected"
# with the verbatim IBKR `{messageId, message, ...}` preserved in
# `raw["reply_messages"]`. The user sees the real IBKR text and decides
# explicitly. The manual paper-account integration run populates this
# tuple with entries pinned by BOTH messageId AND a regex on the message
# text (because IBKR rebinds ids between gateway versions).
#
# Example shape for a future entry:
#     ("o163", re.compile(r"price exceeds the percentage constraint", re.I))

REPLY_ALLOWLIST: tuple[tuple[str, re.Pattern[str]], ...] = ()

REPLY_LOOP_ITERATION_CAP = 10  # hard ceiling — IBKR rarely chains > 3.

# Pinned during the manual paper-account run. The duplicate-cOID error
# text is undocumented; capture it empirically and add a regex here so
# the adapter can resolve the pre-existing order instead of failing.
DUPLICATE_COID_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"duplicate.*cOID", re.I),
    re.compile(r"cOID.*duplicate", re.I),
    re.compile(r"cOID.*already.*used", re.I),
)


# --- IBKR ↔ framework status mapping ----------------------------------------
#
# IBKR statuses observed: Submitted, PreSubmitted, PendingSubmit,
# PendingCancel, Cancelled, Filled, Inactive, Replaced, Rejected.
# An IBKR-side "partial" doesn't exist as a status — we derive it from
# filledQuantity > 0 and < total.

_IBKR_STATUS_MAP: dict[str, str] = {
    "submitted": "submitted",
    "presubmitted": "submitted",
    "pendingsubmit": "submitted",
    "pendingcancel": "submitted",
    "filled": "filled",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "inactive": "cancelled",  # IBKR's catch-all for "no longer working"
    "replaced": "cancelled",
    "rejected": "rejected",
}


def _map_status(ibkr_status: str, filled_qty: Decimal, total_qty: Decimal) -> str:
    base = _IBKR_STATUS_MAP.get(
        (ibkr_status or "").strip().lower(), "error",
    )
    if base in ("submitted",) and filled_qty > 0 and filled_qty < total_qty:
        return "partial"
    return base


# --- Decimal / datetime parsing helpers -------------------------------------


def _dec(v: Any, default: Decimal = Decimal("0")) -> Decimal:
    if v is None or v == "":
        return default
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return default


def _opt_dec(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return None


def _parse_trade_time(raw: Any) -> datetime:
    """IBKR trades carry `trade_time_r` (Unix epoch ms) and/or `trade_time`
    (string like '20260527-13:45:00'). Accept either."""
    if isinstance(raw, int | float) and raw:
        return datetime.fromtimestamp(float(raw) / 1000.0, tz=UTC)
    if isinstance(raw, str) and raw:
        # `YYYYMMDD-HH:MM:SS` (UTC per IBKR docs).
        try:
            return datetime.strptime(raw, "%Y%m%d-%H:%M:%S").replace(
                tzinfo=UTC,
            )
        except ValueError:
            pass
    return timezone.now()


# --- The adapter ------------------------------------------------------------


class IBKRBroker:
    """Per-`BrokerAccount` adapter. Constructed by `_factory(account)`."""

    capabilities = CAPABILITIES

    def __init__(self, account: BrokerAccount) -> None:
        self._account = account
        self._account_id: str = account.account_id
        self._session = IBKRGatewaySession()
        # conid cache lives on the instance — short-lived (one request /
        # one Celery task), so no cross-request leakage of stale conids.
        self._conid_cache: dict[str, int] = {}

    # -- get_account ------------------------------------------------------

    def get_account(self) -> AccountSnapshot:
        """`GET /portfolio/{accountId}/summary` → AccountSnapshot."""
        raw = self._session.get(
            f"/portfolio/{self._account_id}/summary",
        ) or {}
        # IBKR returns a flat map of {field: {amount, currency, ...}}.
        # The fields we want for paper accounts: cash, buying_power, equity.
        def amt(field: str) -> Decimal:
            value = raw.get(field)
            if isinstance(value, dict):
                return _dec(value.get("amount"))
            return _dec(value)

        cash = amt("totalcashvalue") or amt("availablefunds")
        buying_power = amt("buyingpower") or cash
        equity = amt("netliquidation") or cash
        currency = (
            (raw.get("totalcashvalue") or {}).get("currency")
            or self._account.base_currency
            or "USD"
        )
        return AccountSnapshot(
            account_id=self._account_id,
            cash=cash,
            buying_power=buying_power,
            equity=equity,
            currency=currency,
            raw=raw,
        )

    # -- get_positions ----------------------------------------------------

    def get_positions(self) -> list[PositionSnapshot]:
        """Paginated `GET /portfolio/{accountId}/positions/{page}`. IBKR's
        page size is 30 by default; we walk until an empty page comes back
        or we hit a defensive cap of 50 pages (1500 positions)."""
        out: list[PositionSnapshot] = []
        for page in range(50):
            raw = self._session.get(
                f"/portfolio/{self._account_id}/positions/{page}",
            ) or []
            if not raw:
                break
            for row in raw:
                ticker = (
                    row.get("ticker")
                    or row.get("contractDesc")
                    or row.get("conid")
                )
                if not ticker:
                    continue
                qty = _dec(row.get("position"))
                if qty == 0:
                    continue  # IBKR returns 0-qty rows for closed positions
                out.append(
                    PositionSnapshot(
                        ticker=str(ticker).upper(),
                        quantity=qty,  # signed: positive long, negative short
                        avg_cost=_dec(row.get("avgCost")),
                        raw=row,
                    ),
                )
            if len(raw) < 30:
                break  # last page
        return out

    # -- get_recent_fills -------------------------------------------------

    def get_recent_fills(self, since: datetime) -> list[FillSnapshot]:
        """`GET /iserver/account/trades?days=7`. IBKR's window is fixed at
        7 days (today + 6 prior). We fetch the full window and filter
        client-side by `since`. A `since` older than 7 days will miss
        older fills — see phase plan adapter table.
        """
        raw = self._session.get(
            "/iserver/account/trades", params={"days": 7},
        ) or []
        if isinstance(raw, dict):  # defensive: some IBKR versions wrap
            raw = raw.get("trades", []) or []
        fills: list[FillSnapshot] = []
        for row in raw:
            # IBKR scopes by selected account; filter again to be safe.
            row_acc = row.get("account") or row.get("accountId")
            if row_acc and str(row_acc) != self._account_id:
                continue
            filled_at = _parse_trade_time(
                row.get("trade_time_r") or row.get("trade_time"),
            )
            if filled_at < since:
                continue
            execution_id = (
                row.get("execution_id")
                or row.get("execId")
                or row.get("trade_id")
                or ""
            )
            # Fill matching uses orderId (Risks #7a). order_ref echoes
            # the cOID we sent but is not load-bearing here.
            broker_order_id = str(
                row.get("orderId")
                or row.get("order_id")
                or row.get("order_ref")
                or "",
            )
            ticker = str(row.get("symbol") or row.get("ticker") or "").upper()
            side_raw = (row.get("side") or "").upper()
            side = "sell" if side_raw in ("S", "SELL", "SLD") else "buy"
            fills.append(
                FillSnapshot(
                    broker_fill_id=str(execution_id),
                    broker_order_id=broker_order_id,
                    ticker=ticker,
                    quantity=_dec(row.get("size") or row.get("quantity")),
                    price=_dec(row.get("price")),
                    filled_at=filled_at,
                    side=side,
                    raw=row,
                ),
            )
        return fills

    # -- submit_order -----------------------------------------------------

    def submit_order(self, ticket: OrderTicket) -> OrderSnapshot:
        """POST /iserver/account/{accountId}/orders + reply loop. The
        scaffolding's `client_order_id` is sent as IBKR's `cOID`.
        """
        conid = self._resolve_conid(ticket.ticker)
        body = self._build_order_body(ticket, conid)
        try:
            resp = self._session.post(
                f"/iserver/account/{self._account_id}/orders",
                json={"orders": [body]},
            )
        except BrokerError as exc:
            # Risks #7c: a duplicate-cOID rejection means the first order
            # already exists. Try to adopt it via find_order_by_client_id.
            if self._is_duplicate_coid_error(str(exc)):
                existing = self.find_order_by_client_id(ticket.client_order_id)
                if existing is not None:
                    return existing
            raise

        return self._run_reply_loop(
            resp, ticket=ticket, conid=conid,
        )

    def _build_order_body(self, ticket: OrderTicket, conid: int) -> dict:
        body: dict[str, Any] = {
            "conid": conid,
            "secType": "STK",
            "cOID": str(ticket.client_order_id)[:50],  # 50-char safety cap
            "orderType": "MKT" if ticket.order_type == "market" else "LMT",
            "side": "BUY" if ticket.side == "buy" else "SELL",
            "tif": (ticket.time_in_force or "day").upper(),
            "quantity": float(ticket.quantity),
            "outsideRTH": False,
        }
        if ticket.order_type == "limit" and ticket.limit_price is not None:
            body["price"] = float(ticket.limit_price)
        return body

    def _run_reply_loop(
        self,
        initial_response: Any,
        *,
        ticket: OrderTicket,
        conid: int,
    ) -> OrderSnapshot:
        """ADR 0011 §3. Empty allowlist by default — every reply-loop
        message routes to status='rejected' with the IBKR text preserved.
        """
        items = initial_response if isinstance(initial_response, list) else [initial_response]
        collected_messages: list[dict] = []

        for iteration in range(REPLY_LOOP_ITERATION_CAP):
            # If no item in the current response is a confirmation prompt,
            # the order(s) are accepted — return the snapshot.
            prompt = next(
                (i for i in items if isinstance(i, dict) and i.get("id") and (
                    i.get("message") or i.get("messageIds") or i.get("messageId")
                )),
                None,
            )
            if prompt is None:
                # Accepted. The first item should carry order_id / order_status.
                accepted = items[0] if items else {}
                if not isinstance(accepted, dict):
                    raise BrokerError(
                        f"IBKR returned an unparseable submit response: {items!r}",
                    )
                extra_raw = (
                    {"reply_messages": collected_messages}
                    if collected_messages else None
                )
                snap = self._order_response_to_snapshot(
                    accepted,
                    client_order_id=ticket.client_order_id,
                    ticker=ticket.ticker,
                    side=ticket.side,
                    quantity=ticket.quantity,
                    order_type=ticket.order_type,
                    limit_price=ticket.limit_price,
                    time_in_force=ticket.time_in_force,
                    extra_raw=extra_raw,
                )
                return snap

            # We have a confirmation message. Capture it.
            message_id = (
                prompt.get("messageId")
                or (prompt.get("messageIds") or [None])[0]
                or ""
            )
            message_text = prompt.get("message") or ""
            collected_messages.append({
                "messageId": message_id,
                "message": message_text,
                "id": prompt.get("id"),
                "iteration": iteration,
            })

            # Allowlist check: ADR 0011 §3. v1 allowlist is empty.
            allowed = any(
                str(message_id) == allow_id and pattern.search(message_text or "")
                for allow_id, pattern in REPLY_ALLOWLIST
            )
            if not allowed:
                return self._rejection_snapshot(
                    ticket=ticket,
                    reason="reply_loop_blocking_message",
                    reply_messages=collected_messages,
                )

            # Auto-reply confirmed=true and loop.
            try:
                next_resp = self._session.reply(
                    str(prompt.get("id")), confirmed=True,
                )
            except BrokerError as exc:
                return self._rejection_snapshot(
                    ticket=ticket,
                    reason=f"reply_loop_reply_failed: {exc}",
                    reply_messages=collected_messages,
                )
            items = next_resp if isinstance(next_resp, list) else [next_resp]

        # Hit the iteration cap. Surface as a rejection.
        return self._rejection_snapshot(
            ticket=ticket,
            reason="reply_loop_iteration_cap",
            reply_messages=collected_messages,
        )

    def _rejection_snapshot(
        self,
        *,
        ticket: OrderTicket,
        reason: str,
        reply_messages: list[dict],
    ) -> OrderSnapshot:
        return OrderSnapshot(
            broker_order_id="",
            client_order_id=ticket.client_order_id,
            ticker=ticket.ticker,
            side=ticket.side,
            quantity=ticket.quantity,
            order_type=ticket.order_type,
            limit_price=ticket.limit_price,
            time_in_force=ticket.time_in_force or "day",
            status="rejected",
            filled_quantity=Decimal("0"),
            avg_fill_price=None,
            raw={"reply_messages": reply_messages, "error": reason},
        )

    @staticmethod
    def _is_duplicate_coid_error(text: str) -> bool:
        return any(p.search(text) for p in DUPLICATE_COID_PATTERNS)

    # -- get_order --------------------------------------------------------

    def get_order(self, broker_order_id: str) -> OrderSnapshot:
        """Match `broker_order_id` against `/iserver/account/orders` first.
        If absent (terminal orders roll off — see Risks #7b), fall back to
        `/iserver/account/trades?days=7` and derive the terminal status
        there."""
        orders_resp = self._session.get("/iserver/account/orders") or {}
        orders = orders_resp.get("orders") if isinstance(orders_resp, dict) else orders_resp
        if not isinstance(orders, list):
            orders = []
        for raw in orders:
            if str(raw.get("orderId")) == str(broker_order_id):
                return self._raw_order_to_snapshot(raw)

        # Fallback: derive from trades.
        fills = self.get_recent_fills(timezone.now() - _SEVEN_DAYS)
        order_fills = [f for f in fills if str(f.broker_order_id) == str(broker_order_id)]
        if order_fills:
            total_filled = sum((f.quantity for f in order_fills), Decimal("0"))
            avg_price = (
                sum((f.price * f.quantity for f in order_fills), Decimal("0"))
                / total_filled
            ) if total_filled else None
            first = order_fills[0]
            return OrderSnapshot(
                broker_order_id=str(broker_order_id),
                client_order_id="",
                ticker=first.ticker,
                side=first.side,
                quantity=total_filled,
                order_type="market",
                limit_price=None,
                time_in_force="day",
                status="filled",
                filled_quantity=total_filled,
                avg_fill_price=avg_price,
                raw={"derived_from": "trades_fallback"},
            )
        raise BrokerError(f"IBKR order {broker_order_id} not found")

    # -- cancel_order -----------------------------------------------------

    def cancel_order(self, broker_order_id: str) -> None:
        self._session.delete(
            f"/iserver/account/{self._account_id}/order/{broker_order_id}",
        )

    # -- find_order_by_client_id -----------------------------------------

    def find_order_by_client_id(
        self, client_order_id: str, *, order_meta=None,
    ) -> OrderSnapshot | None:
        """ADR 0008 unknown-response adoption contract. `cOID` is reliably
        echoed only on `/iserver/account/orders` (Risks #7a)."""
        orders_resp = self._session.get("/iserver/account/orders") or {}
        orders = (
            orders_resp.get("orders") if isinstance(orders_resp, dict) else orders_resp
        )
        if not isinstance(orders, list):
            orders = []
        target = str(client_order_id)
        for raw in orders:
            if str(raw.get("cOID") or raw.get("order_ref") or "") == target:
                return self._raw_order_to_snapshot(raw)
        return None

    # -- conid resolution -------------------------------------------------

    def _resolve_conid(self, ticker: str) -> int:
        """`GET /iserver/secdef/search?symbol=...` → first STK match.
        Cached on this adapter instance."""
        key = ticker.upper().strip()
        if key in self._conid_cache:
            return self._conid_cache[key]
        raw = self._session.get(
            "/iserver/secdef/search", params={"symbol": key, "name": "false"},
        ) or []
        if not isinstance(raw, list):
            raw = []
        # Look for the first US-equity match. `sections` carries secType per
        # row; fall back to `assetClass`/`secType` on the row itself.
        for row in raw:
            sections = row.get("sections") or []
            section_types = [
                str(s.get("secType") or "").upper()
                for s in sections if isinstance(s, dict)
            ]
            row_type = str(row.get("assetClass") or row.get("secType") or "").upper()
            if "STK" in section_types or row_type == "STK":
                conid = row.get("conid")
                if conid:
                    self._conid_cache[key] = int(conid)
                    return int(conid)
        raise BrokerError(f"could not resolve conid for ticker {key!r}")

    # -- raw → DTO --------------------------------------------------------

    def _raw_order_to_snapshot(self, raw: dict) -> OrderSnapshot:
        total_qty = _dec(
            raw.get("totalSize") or raw.get("quantity") or raw.get("origQuantity"),
        )
        filled_qty = _dec(raw.get("filledQuantity") or raw.get("cumFill"))
        status = _map_status(
            str(raw.get("status") or raw.get("order_status") or ""),
            filled_qty, total_qty,
        )
        side_raw = (raw.get("side") or "").upper()
        side = "sell" if side_raw in ("S", "SELL", "SLD") else "buy"
        order_type_raw = (raw.get("orderType") or "").upper()
        order_type = "limit" if "LMT" in order_type_raw else "market"
        return OrderSnapshot(
            broker_order_id=str(raw.get("orderId") or raw.get("order_id") or ""),
            client_order_id=str(raw.get("cOID") or raw.get("order_ref") or ""),
            ticker=str(raw.get("ticker") or raw.get("symbol") or "").upper(),
            side=side,
            quantity=total_qty,
            order_type=order_type,
            limit_price=_opt_dec(raw.get("price")),
            time_in_force=str(raw.get("timeInForce") or raw.get("tif") or "day").lower(),
            status=status,
            filled_quantity=filled_qty,
            avg_fill_price=_opt_dec(raw.get("avgPrice") or raw.get("averagePrice")),
            raw=raw,
        )

    def _order_response_to_snapshot(
        self,
        accepted: dict,
        *,
        client_order_id: str,
        ticker: str,
        side: str,
        quantity: Decimal,
        order_type: str,
        limit_price: Decimal | None,
        time_in_force: str,
        extra_raw: dict | None = None,
    ) -> OrderSnapshot:
        """Map the post-orders accepted response → OrderSnapshot. The
        post-orders body is sparser than /iserver/account/orders; the
        caller's `ticket` fills the gaps."""
        broker_order_id = str(
            accepted.get("order_id") or accepted.get("orderId") or "",
        )
        ibkr_status = str(
            accepted.get("order_status") or accepted.get("orderStatus") or "submitted",
        )
        status = _map_status(ibkr_status, Decimal("0"), quantity)
        raw = dict(accepted)
        if extra_raw:
            raw.update(extra_raw)
        return OrderSnapshot(
            broker_order_id=broker_order_id,
            client_order_id=client_order_id,
            ticker=ticker,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            time_in_force=time_in_force or "day",
            status=status,
            filled_quantity=Decimal("0"),
            avg_fill_price=None,
            raw=raw,
        )


# Shared constant for the trades-fallback window in get_order.
from datetime import timedelta as _td  # noqa: E402

_SEVEN_DAYS = _td(days=7)


# --- Registration -----------------------------------------------------------


def _factory(account: BrokerAccount) -> IBKRBroker:
    return IBKRBroker(account)


register_broker(CAPABILITIES, _factory)
