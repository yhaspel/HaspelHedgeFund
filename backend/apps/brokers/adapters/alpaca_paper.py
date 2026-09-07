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
    BrokerAuthError,
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
    supported_order_types=(
        "market", "limit", "stop", "stop_limit", "trailing_stop",
    ),
    supported_time_in_force=("day", "gtc"),
    supports_bracket=True,
    description=(
        "Alpaca paper Trading API. API key id + secret. Verified end-to-end "
        "against a live Alpaca paper sandbox 2026-05-29; bracket / OTO / OCO / "
        "stop / stop-limit / trailing-stop verified live 2026-06-03."
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


# Alpaca `OrderType` → framework order_type. Note the exact-key match: the old
# substring test ("limit" in raw) wrongly collapsed `stop_limit` → limit and
# `stop` → market. Unknown types fall back to `market` (never matching a
# protective leg), which is fine for the supported shapes.
_ORDER_TYPE_MAP: dict[str, str] = {
    "market": "market",
    "limit": "limit",
    "stop": "stop",
    "stop_limit": "stop_limit",
    "trailing_stop": "trailing_stop",
}


def _map_order_type(raw: Any) -> str:
    return _ORDER_TYPE_MAP.get(_enum_value(raw).lower(), "market")


def _leg_role_from_type(order_type: str) -> str:
    """Infer a protective leg's role from its order type: a plain limit is the
    take-profit; a stop / stop-limit / trailing is the stop-loss. The
    reconcile layer keys on the same signal (see ADR 0015)."""
    if order_type == "limit":
        return "take_profit"
    if order_type in ("stop", "stop_limit", "trailing_stop"):
        return "stop_loss"
    return ""


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
        """Block until a call slot is free.

        The wait happens OUTSIDE the lock: sleeping while holding it serialized
        every other thread behind the one waiter (a single throttled Celery
        worker stalled the web process's manual sync for a whole window), and
        the sleeping thread's slot was never actually reserved anyway."""
        while True:
            with self._lock:
                now = time.monotonic()
                while self._stamps and now - self._stamps[0] > self.window_s:
                    self._stamps.popleft()
                if len(self._stamps) < self.max_calls:
                    self._stamps.append(now)
                    return
                sleep_for = self.window_s - (now - self._stamps[0]) + 0.01
            if sleep_for > 0:
                time.sleep(sleep_for)


_LIMITER = _RateLimiter(max_calls=180, window_s=60.0)


# --- SDK client construction -------------------------------------------------


# alpaca-py never passes `timeout=` to requests, so a hung socket blocks the
# calling worker forever — a gunicorn worker on a manual sync, a Celery worker
# on the 30 s poll. (connect, read) seconds, applied to the SDK's own session.
HTTP_TIMEOUT: tuple[float, float] = (10.0, 30.0)


def _apply_http_timeout(client):
    """Force a (connect, read) timeout onto the SDK client's requests session.

    The SDK calls `self._session.request(method, url, **opts)` and never sets
    `timeout`, so we wrap the session's `request` and inject a default. Done on
    the session (not per call) so every present and future SDK endpoint is
    covered. Idempotent — re-wrapping a session is a no-op."""
    session = getattr(client, "_session", None)
    if session is None or getattr(session, "_hhf_timeout_applied", False):
        return client
    inner = session.request

    def _request_with_timeout(*args, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = HTTP_TIMEOUT
        return inner(*args, **kwargs)

    session.request = _request_with_timeout
    session._hhf_timeout_applied = True
    return client


def _make_client(api_key: str, api_secret: str):
    """Construct an Alpaca `TradingClient` bound to the paper host.

    Imported lazily so test code can patch `_make_client` without paying
    the SDK import cost during collection.
    """
    from alpaca.trading.client import TradingClient

    # paper=True is the only path. url_override is never set — that
    # parameter could be used to point at the live host, so we don't
    # expose it through any code path. ADR 0013 §1.
    return _apply_http_timeout(
        TradingClient(api_key=api_key, secret_key=api_secret, paper=True),
    )


# --- Exception translation --------------------------------------------------


# Alpaca returns HTTP 403 for BOTH genuine auth walls and certain ORDER
# rejections (notably 40310000 "insufficient qty available for order"). The
# latter must terminate as a rejection, not be mistaken for an auth blip and
# walked back to a re-submittable `confirmed` state where it lingers forever
# (idempotency.py BrokerAuthError path). Distinguish by the body's `code`.
_ORDER_REJECT_403_CODES = frozenset({40310000})


def _coerce_int(value) -> int | None:
    """int(...) that tolerates int/str/None ('40310000' → 40310000)."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _alpaca_error_code(exc: Exception) -> int | None:
    """Best-effort extraction of Alpaca's numeric error ``code`` from an
    APIError (direct attribute, then a JSON body parse). Tolerates the code
    arriving as a string."""
    import json

    # ``APIError.code`` is a PROPERTY doing ``json.loads(self._error)["code"]``,
    # so it raises KeyError when the body carries no ``code`` and
    # json.JSONDecodeError (a ValueError) when the body is not JSON at all —
    # neither of which getattr's default swallows (it only covers
    # AttributeError). An unguarded read escaped this helper and killed
    # _raise_translated before it could reach the 401/403 arm (the account
    # stayed `active` instead of flipping to needs_reauth), and a proxy's HTML
    # error page blew up the same way one function over.
    try:
        code = _coerce_int(getattr(exc, "code", None))
    except (KeyError, AttributeError, TypeError, ValueError):
        code = None
    if code is not None:
        return code
    for blob in (getattr(exc, "_error", None), str(exc)):
        if not blob:
            continue
        try:
            data = json.loads(blob if isinstance(blob, str) else json.dumps(blob))
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            code = _coerce_int(data.get("code"))
            if code is not None:
                return code
    return None


def _error_text(exc: Exception, limit: int = 200) -> str:
    """The raw error body, truncated. Never raises: an APIError whose body is
    an HTML proxy page has no ``.message``/``.code`` to read (both properties
    json.loads the body), so we fall back to ``str(exc)``."""
    try:
        text = str(getattr(exc, "message", None) or exc)
    except Exception:  # noqa: BLE001 — the property itself is what failed
        text = str(exc)
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _raise_translated(exc: Exception, op: str) -> None:
    """Translate Alpaca SDK errors into framework exceptions.

    Definitive (`BrokerError` / `BrokerAuthError`) vs transient
    (`BrokerTransientError`) is the whole point: a definitive answer terminates
    the order, a transient one must leave its state alone and be retried. This
    function must NEVER raise anything else — a body it cannot parse is exactly
    the case where a raised `KeyError`/`JSONDecodeError` stranded the order
    between two arms of the state machine.
    """
    # Lazy import so the SDK isn't loaded at module import time.
    from alpaca.common.exceptions import APIError

    if isinstance(exc, APIError):
        status_code = getattr(exc, "status_code", None) or 0
        code = _alpaca_error_code(exc)
        body = _error_text(exc)
        # An order-domain 403 (e.g. insufficient qty) is the venue refusing the
        # ORDER, not an auth failure — classify as a hard rejection so it
        # terminates rather than walking back to a never-retried `confirmed`.
        if status_code == 403 and code in _ORDER_REJECT_403_CODES:
            raise BrokerError(f"Alpaca {op} → {status_code}: {body}") from exc
        if status_code in (401, 403):
            raise BrokerAuthError(f"Alpaca {op} → {status_code}: {body}") from exc
        # 429 (throttled) and 5xx (upstream) are retryable by definition.
        if status_code == 429 or status_code >= 500:
            raise BrokerTransientError(f"Alpaca {op} → {status_code}: {body}") from exc
        if code is None:
            # A 4xx we cannot parse is NOT a documented venue rejection — it is
            # an HTML error page from a proxy/CDN in front of Alpaca, or a
            # truncated body. Treating it as definitive rejected live orders
            # that the venue had actually accepted, so classify it as transient
            # and let the reconcile loop ask the venue what really happened.
            raise BrokerTransientError(
                f"Alpaca {op} → {status_code} (unparseable body): {body}",
            ) from exc
        raise BrokerError(f"Alpaca {op} → {status_code}: {body}") from exc
    raise BrokerTransientError(f"Alpaca {op} failed: {exc}") from exc


def _is_duplicate_client_order_id(exc: Exception) -> bool:
    """Alpaca rejects a second POST /v2/orders with the same
    client_order_id with HTTP 422 and a message like 'client_order_id
    must be unique'. Match defensively on both signals.

    Must never raise: `APIError.message` is a property that json.loads the
    body, so a non-JSON 422 (HTML error page) used to blow up here — before
    either the transient or the rejection arm of the submit state machine could
    run — and left the order stuck in `submit_pending` forever."""
    from alpaca.common.exceptions import APIError

    if not isinstance(exc, APIError):
        return False
    if getattr(exc, "status_code", None) != 422:
        return False
    msg = _error_text(exc, limit=2000).lower()
    return "client_order_id" in msg and (
        "unique" in msg or "exists" in msg or "duplicate" in msg
    )


def _is_transient(exc: Exception) -> bool:
    """True when `exc` says nothing definitive about the request's outcome."""
    from alpaca.common.exceptions import APIError

    if not isinstance(exc, APIError):
        return True  # socket timeout, connection reset, DNS…
    status_code = getattr(exc, "status_code", None) or 0
    if status_code == 429 or status_code >= 500:
        return True
    # A 4xx whose body carries no Alpaca error code is a proxy/CDN page, not a
    # documented venue rejection.
    return 400 <= status_code < 500 and _alpaca_error_code(exc) is None


# Transient failures are retried in place with a short exponential backoff, so
# a single throttle or upstream blip doesn't cost a whole 30 s poll cycle.
# READ-ONLY calls only: a retried POST is Alpaca's own idempotency problem
# (client_order_id → 422), handled in `_do_submit`.
TRANSIENT_RETRIES = 2
TRANSIENT_BACKOFF_S = 0.5


def _read_call(op: str, fn, *, none_on_404: bool = False):
    """Run a read-only Alpaca call under the rate limiter, retrying transient
    failures with backoff and translating whatever survives."""
    from alpaca.common.exceptions import APIError

    retries = TRANSIENT_RETRIES
    delay = TRANSIENT_BACKOFF_S
    for attempt in range(retries + 1):
        _LIMITER.acquire()
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — classified below
            if (
                none_on_404
                and isinstance(exc, APIError)
                and getattr(exc, "status_code", None) == 404
            ):
                return None
            if attempt >= retries or not _is_transient(exc):
                _raise_translated(exc, op)
            log.warning(
                "Alpaca %s transient failure (%s) — retry %d/%d in %.1fs",
                op, _error_text(exc, limit=120), attempt + 1, retries, delay,
            )
            time.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")  # pragma: no cover


# --- The adapter ------------------------------------------------------------


class AlpacaPaperBroker:
    capabilities = CAPABILITIES

    def __init__(self, account: BrokerAccount) -> None:
        assert_paper(account)
        self._account = account
        self._client = _resolve_client(account)

    # -- get_account ------------------------------------------------------

    def get_account(self) -> AccountSnapshot:
        row = _read_call("get_account", self._client.get_account)
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
        rows = _read_call("get_all_positions", self._client.get_all_positions) or []
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

        req = GetOrdersRequest(
            status=QueryOrderStatus.CLOSED, after=since, limit=500,
        )
        rows = _read_call(
            "get_orders(closed)", lambda: self._client.get_orders(filter=req),
        ) or []
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

    # -- get_portfolio_history ---------------------------------------------
    #
    # P10 §C3: Alpaca's GET /v2/account/portfolio/history gives daily account
    # equity since (near) account creation — instant NAV history without
    # fills+bars reconstruction. Consumed by the backfill_portfolio_history
    # management command, which upserts PortfolioSnapshot rows (flows are
    # derived from the ledger there, not here).

    def get_portfolio_history(
        self, *, period: str = "1A", timeframe: str = "1D",
    ) -> list[tuple[datetime, Decimal]]:
        """Daily (timestamp, equity) points from Alpaca portfolio history.
        Zero/None equity points (pre-funding placeholders) are dropped."""
        from alpaca.trading.requests import GetPortfolioHistoryRequest

        req = GetPortfolioHistoryRequest(period=period, timeframe=timeframe)
        hist = _read_call(
            "get_portfolio_history",
            lambda: self._client.get_portfolio_history(history_filter=req),
        )
        stamps = list(getattr(hist, "timestamp", None) or [])
        equities = list(getattr(hist, "equity", None) or [])
        out: list[tuple[datetime, Decimal]] = []
        for ts, eq in zip(stamps, equities, strict=False):
            if eq in (None, 0, 0.0):
                continue
            # The endpoint returns epoch SECONDS; tolerate datetimes/strings too.
            when = (
                datetime.fromtimestamp(ts, tz=UTC)
                if isinstance(ts, int | float)
                else _to_dt(ts)
            )
            out.append((when, _dec(eq)))
        return out

    # -- submit_order -----------------------------------------------------

    def _common_kwargs(self, ticket: OrderTicket) -> dict:
        from alpaca.trading.enums import OrderSide, TimeInForce

        side = OrderSide.BUY if ticket.side == "buy" else OrderSide.SELL
        tif = (
            TimeInForce.GTC
            if (ticket.time_in_force or "day") == "gtc"
            else TimeInForce.DAY
        )
        return dict(
            symbol=ticket.ticker.upper(),
            qty=float(ticket.quantity),
            side=side,
            time_in_force=tif,
            client_order_id=ticket.client_order_id,
        )

    def _build_simple_request(self, ticket: OrderTicket):
        """Map a standalone OrderTicket onto the right SDK request class. Each
        type fails fast on a missing required price — this is what closes the
        latent "stop silently becomes a market order" path."""
        from alpaca.trading.requests import (
            LimitOrderRequest,
            MarketOrderRequest,
            StopLimitOrderRequest,
            StopOrderRequest,
            TrailingStopOrderRequest,
        )

        common = self._common_kwargs(ticket)
        ot = ticket.order_type
        if ot == "limit":
            if ticket.limit_price is None:
                raise BrokerError("limit order requires limit_price")
            return LimitOrderRequest(limit_price=float(ticket.limit_price), **common)
        if ot == "stop":
            if ticket.stop_price is None:
                raise BrokerError("stop order requires stop_price")
            return StopOrderRequest(stop_price=float(ticket.stop_price), **common)
        if ot == "stop_limit":
            if ticket.stop_price is None or ticket.limit_price is None:
                raise BrokerError("stop_limit order requires stop_price and limit_price")
            return StopLimitOrderRequest(
                stop_price=float(ticket.stop_price),
                limit_price=float(ticket.limit_price),
                **common,
            )
        if ot == "trailing_stop":
            has_price = ticket.trail_price is not None
            has_percent = ticket.trail_percent is not None
            if has_price == has_percent:  # neither or both
                raise BrokerError(
                    "trailing_stop requires exactly one of trail_price / trail_percent",
                )
            if has_price:
                return TrailingStopOrderRequest(
                    trail_price=float(ticket.trail_price), **common,
                )
            return TrailingStopOrderRequest(
                trail_percent=float(ticket.trail_percent), **common,
            )
        return MarketOrderRequest(**common)

    def _exit_legs(self, ticket: OrderTicket) -> tuple[Any, Any]:
        """Build the (take_profit, stop_loss) SDK leg DTOs from a carrier
        ticket. Either may be None (OTO carries exactly one)."""
        from alpaca.trading.requests import StopLossRequest, TakeProfitRequest

        take_profit = None
        if ticket.take_profit_limit_price is not None:
            take_profit = TakeProfitRequest(
                limit_price=float(ticket.take_profit_limit_price),
            )
        stop_loss = None
        if ticket.stop_loss_stop_price is not None:
            sl_kwargs: dict = {"stop_price": float(ticket.stop_loss_stop_price)}
            if ticket.stop_loss_limit_price is not None:
                sl_kwargs["limit_price"] = float(ticket.stop_loss_limit_price)
            stop_loss = StopLossRequest(**sl_kwargs)
        return take_profit, stop_loss

    def _do_submit(self, req: Any, ticket: OrderTicket) -> OrderSnapshot:
        _LIMITER.acquire()
        try:
            order = self._client.submit_order(order_data=req)
        except Exception as exc:  # noqa: BLE001
            # Native idempotency: a retried POST with the same
            # client_order_id returns 422. Treat that as proof the first
            # order already exists, resolve it (with its legs[] for a
            # grouped order), and surface the existing snapshot.
            if _is_duplicate_client_order_id(exc):
                existing = self.find_order_by_client_id(ticket.client_order_id)
                if existing is not None:
                    return existing
            _raise_translated(exc, "submit_order")
        return self._snapshot_from_order(order, fallback_ticket=ticket)

    def submit_order(self, ticket: OrderTicket) -> OrderSnapshot:
        return self._do_submit(self._build_simple_request(ticket), ticket)

    # -- submit_bracket / submit_protective -------------------------------

    def submit_bracket(self, ticket: OrderTicket) -> OrderSnapshot:
        """Submit an entry-carrying bracket / OTO ticket. The entry is a
        market or limit base request; `order_class` + nested take-profit /
        stop-loss legs ride on it. Returns the parent snapshot with `.legs`."""
        from alpaca.trading.enums import OrderClass
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

        take_profit, stop_loss = self._exit_legs(ticket)
        if ticket.order_class == "bracket":
            if take_profit is None or stop_loss is None:
                raise BrokerError("bracket requires both take_profit and stop_loss")
            order_class = OrderClass.BRACKET
        elif ticket.order_class == "oto":
            if (take_profit is None) == (stop_loss is None):
                raise BrokerError("oto requires exactly one protective leg")
            order_class = OrderClass.OTO
        else:
            raise BrokerError(f"submit_bracket got order_class {ticket.order_class!r}")

        common = self._common_kwargs(ticket)
        extra: dict = {"order_class": order_class}
        if take_profit is not None:
            extra["take_profit"] = take_profit
        if stop_loss is not None:
            extra["stop_loss"] = stop_loss
        if ticket.order_type == "limit":
            if ticket.limit_price is None:
                raise BrokerError("limit entry requires limit_price")
            req = LimitOrderRequest(
                limit_price=float(ticket.limit_price), **common, **extra,
            )
        else:
            req = MarketOrderRequest(**common, **extra)
        return self._do_submit(req, ticket)

    def submit_protective(self, ticket: OrderTicket) -> OrderSnapshot:
        """Submit a standalone OCO pair against a held position: a take-profit
        limit base request with `order_class=OCO` carrying the stop-loss leg.
        The take-profit IS the parent; the stop-loss comes back in `.legs`. A
        single standalone stop/stop_limit/trailing goes through `submit_order`."""
        from alpaca.trading.enums import OrderClass
        from alpaca.trading.requests import LimitOrderRequest

        take_profit, stop_loss = self._exit_legs(ticket)
        if take_profit is None or stop_loss is None:
            raise BrokerError("oco requires both take_profit and stop_loss")
        common = self._common_kwargs(ticket)
        # Alpaca OCO wants BOTH the base limit (the take-profit) AND a nested
        # take_profit leg — it rejects with 422 "oco orders require
        # take_profit.limit_price" if the take_profit object is omitted.
        req = LimitOrderRequest(
            limit_price=float(ticket.take_profit_limit_price),
            order_class=OrderClass.OCO,
            take_profit=take_profit,
            stop_loss=stop_loss,
            **common,
        )
        return self._do_submit(req, ticket)

    # -- get_order --------------------------------------------------------

    def get_order(self, broker_order_id: str) -> OrderSnapshot:
        order = _read_call(
            f"get_order_by_id({broker_order_id})",
            lambda: self._client.get_order_by_id(order_id=broker_order_id),
        )
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
        # SDK uses positional `client_id` arg (not `client_order_id`), despite
        # the corresponding submit_order field being named `client_order_id`.
        # Verified against alpaca-py 0.43.4 via the live-sandbox checklist.
        # A 404 means "Alpaca has no such order" — an ANSWER, not a failure.
        order = _read_call(
            f"get_order_by_client_id({client_order_id})",
            lambda: self._client.get_order_by_client_id(client_order_id),
            none_on_404=True,
        )
        if order is None:
            return None
        return self._snapshot_from_order(order)

    # -- SDK row → DTO ----------------------------------------------------

    def _snapshot_from_order(
        self,
        order: Any,
        *,
        fallback_ticket: OrderTicket | None = None,
        as_leg: bool = False,
    ) -> OrderSnapshot:
        order_type = _map_order_type(
            getattr(order, "order_type", None) or getattr(order, "type", None),
        )
        side_raw = _enum_value(getattr(order, "side", "")).lower()
        side = "sell" if side_raw.startswith("sell") else "buy"
        tif_raw = _enum_value(getattr(order, "time_in_force", "")).lower() or "day"
        quantity = _dec(
            getattr(order, "qty", None)
            or (fallback_ticket.quantity if fallback_ticket else None),
        )
        status = _map_status(getattr(order, "status", None))
        order_class = _enum_value(getattr(order, "order_class", None)).lower() or "simple"

        # leg_role: a leg infers its role from its own type; the parent's role
        # follows the order_class (entry for bracket/oto, take_profit for oco).
        if as_leg:
            leg_role = _leg_role_from_type(order_type)
        elif order_class in ("bracket", "oto"):
            leg_role = "entry"
        elif order_class == "oco":
            leg_role = "take_profit"
        else:
            leg_role = ""

        # `legs` is Optional[List] on the SDK model — guard for None. One level
        # of nesting only; a leg carries no further legs.
        legs: list[OrderSnapshot] = []
        if not as_leg:
            for leg in getattr(order, "legs", None) or []:
                legs.append(self._snapshot_from_order(leg, as_leg=True))

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
            stop_price=_opt_dec(getattr(order, "stop_price", None)),
            trail_price=_opt_dec(getattr(order, "trail_price", None)),
            trail_percent=_opt_dec(getattr(order, "trail_percent", None)),
            order_class=order_class,
            leg_role=leg_role,
            legs=legs,
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
    if isinstance(v, str | int | float | bool) or v is None:
        return v
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if hasattr(v, "value"):
        return v.value
    if isinstance(v, list | tuple):
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
