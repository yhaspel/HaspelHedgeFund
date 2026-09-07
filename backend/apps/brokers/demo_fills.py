"""Demo (mock-broker) order fill engine — P3a-1 polish.

The demo broker has no external venue, so the database is its own book of
record. This module evaluates demo orders against the live market price
and fills them straight into the broker Portfolio via the shared
``reconcile._ingest_fill`` -> ``_apply_fill_to_portfolio`` path. It never
relies on the in-process ``MockBroker`` state, so a resting order placed
in the web process is just as visible to the Celery worker that later
fills it — both read the same ``BrokerOrder`` rows.

Lifecycle for a demo account:
  - Market order : fills immediately at the live price.
  - Limit order  : rests as ``submitted`` until the live price crosses the
                   limit, then fills at the limit price.
  - Stop order   : rests as ``submitted`` until the live price crosses the
                   stop, then fills at market (the live price).

``place_demo_order`` runs synchronously on order creation (so marketable
orders fill within the request). ``evaluate_resting_demo_orders`` is the
periodic re-check, called from the ``poll_open_orders`` Celery beat task
and from a manual "Sync now".
"""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from django.utils import timezone

from apps.data.models import DailyBar

from .interfaces import FillSnapshot
from .models import BrokerAccount, BrokerOrder
from .reconcile import _ingest_fill

log = logging.getLogger(__name__)

ORDER_TYPE_MARKET = "market"
ORDER_TYPE_LIMIT = "limit"
ORDER_TYPE_STOP = "stop"
DEMO_ORDER_TYPES = (ORDER_TYPE_MARKET, ORDER_TYPE_LIMIT, ORDER_TYPE_STOP)


def live_price(account: BrokerAccount, ticker: str) -> Decimal | None:
    """Best-available current price for ``ticker``.

    Primary source is the live FMP quote (the same feed behind the
    watchlist); falls back to the most recent cached daily bar so the demo
    still works when the quote provider is unavailable.
    """
    sym = ticker.upper()
    try:
        from apps.data.providers.factory import get_fmp_provider

        fmp = get_fmp_provider(user=account.user)
        quote = fmp.get_latest_quote(sym)
        if quote is not None:
            price = Decimal(str(quote[0]))
            if price > 0:
                return price
    except Exception as exc:  # noqa: BLE001 — degrade to the cached bar
        log.warning("demo live_price quote failure ticker=%s err=%s", sym, exc)
    bar = DailyBar.objects.filter(ticker=sym).order_by("-date").first()
    if bar is not None and bar.close and bar.close > 0:
        return Decimal(str(bar.close))
    return None


def _fill_decision(order: BrokerOrder, price: Decimal) -> Decimal | None:
    """The fill price if ``order`` should fill at market ``price``, else None."""
    side = order.side
    otype = order.order_type
    if otype == ORDER_TYPE_MARKET:
        return price
    if otype == ORDER_TYPE_LIMIT:
        limit = order.limit_price
        if limit is None:
            return None
        # Buy fills at/below the limit; sell at/above it. Fill AT the limit
        # price — deterministic and never worse than the trigger.
        if side == "buy" and price <= limit:
            return limit
        if side == "sell" and price >= limit:
            return limit
        return None
    if otype == ORDER_TYPE_STOP:
        stop = order.stop_price
        if stop is None:
            return None
        # A stop becomes a market order once the trigger is crossed: a buy
        # stop triggers on the way up, a sell stop on the way down.
        if side == "buy" and price >= stop:
            return price
        if side == "sell" and price <= stop:
            return price
        return None
    return None


def _book_cash(order: BrokerOrder) -> Decimal:
    """Cash on the demo book, read fresh — a cached `portfolio` on the order's
    account goes stale the moment a sibling order in the same poll fills."""
    from apps.portfolios.models import Portfolio

    cash = (
        Portfolio.objects
        .filter(pk=order.broker_account.portfolio_id)
        .values_list("cash_balance", flat=True)
        .first()
    )
    return Decimal(str(cash or 0))


def insufficient_cash(order: BrokerOrder, fill_px: Decimal) -> Decimal | None:
    """The shortfall if this BUY would overdraw the demo book, else ``None``.

    The demo account is a cash book with no margin: a real broker refuses a
    buy it cannot fund, and without this check the book simply went negative
    (a 100k demo account "bought" $20M of AAPL and reported -$19.9M cash,
    poisoning every NAV, drift and exposure number downstream). Sells are
    unrestricted — a short is a legitimate demo position."""
    if order.side != "buy":
        return None
    notional = (order.quantity * fill_px).quantize(Decimal("0.01"))
    cash = _book_cash(order)
    if notional <= cash:
        return None
    return notional - cash


def _reject_for_cash(order: BrokerOrder, fill_px: Decimal, shortfall: Decimal) -> None:
    now = timezone.now()
    message = (
        f"insufficient buying power: {order.quantity} {order.ticker} @ "
        f"{fill_px} needs {(order.quantity * fill_px).quantize(Decimal('0.01'))} "
        f"but the account holds {_book_cash(order)} (short by {shortfall})"
    )
    BrokerOrder.objects.filter(pk=order.pk).update(
        status=BrokerOrder.STATUS_REJECTED,
        error_message=message[:500],
        cancelled_at=now,
    )
    order.refresh_from_db()
    log.warning("demo order rejected id=%s — %s", order.pk, message)


def try_fill_demo_order(order: BrokerOrder, *, price: Decimal | None = None) -> bool:
    """Evaluate one demo order against the live price; fill it if eligible.

    Returns True when a fill was recorded. Safe to call repeatedly — once
    the order is ``filled`` it is skipped.
    """
    if order.status not in (
        BrokerOrder.STATUS_SUBMITTED,
        BrokerOrder.STATUS_PARTIAL,
    ):
        return False
    if price is None:
        price = live_price(order.broker_account, order.ticker)
    if price is None or price <= 0:
        return False

    fill_px = _fill_decision(order, price)
    if fill_px is None:
        return False
    fill_px = Decimal(str(fill_px)).quantize(Decimal("0.0001"))

    shortfall = insufficient_cash(order, fill_px)
    if shortfall is not None:
        _reject_for_cash(order, fill_px, shortfall)
        return False

    snapshot = FillSnapshot(
        broker_fill_id=f"DEMOFILL-{uuid.uuid4().hex[:12]}",
        broker_order_id=order.broker_order_id or order.client_order_id,
        ticker=order.ticker,
        quantity=order.quantity,
        price=fill_px,
        filled_at=timezone.now(),
        side=order.side,
    )
    _ingest_fill(order, snapshot)
    now = timezone.now()
    BrokerOrder.objects.filter(pk=order.pk).update(
        status=BrokerOrder.STATUS_FILLED,
        filled_quantity=order.quantity,
        avg_fill_price=fill_px,
        filled_at=now,
        submitted_at=order.submitted_at or now,
    )
    order.refresh_from_db()
    log.info(
        "demo order filled id=%s %s %s x%s @ %s",
        order.pk, order.side, order.ticker, order.quantity, fill_px,
    )
    return True


def place_demo_order(order: BrokerOrder, *, user=None) -> None:
    """Submit a freshly created demo order.

    Skips the draft/confirm gate (a demo has no live-trading risk), marks
    the order ``submitted``, then attempts an immediate fill — market
    orders and marketable limit/stop orders fill right away; the rest rest.
    """
    now = timezone.now()
    order.status = BrokerOrder.STATUS_SUBMITTED
    order.idempotency_state = BrokerOrder.IDEM_ACKNOWLEDGED
    order.broker_order_id = f"DEMO-{uuid.uuid4().hex[:12]}"
    order.submitted_at = now
    order.confirmed_at = now
    order.confirmation_method = BrokerOrder.CONFIRM_MANUAL
    if user is not None:
        order.confirmed_by = user
    order.save(update_fields=[
        "status", "idempotency_state", "broker_order_id", "submitted_at",
        "confirmed_at", "confirmation_method", "confirmed_by",
    ])
    try:
        try_fill_demo_order(order)
    except Exception:  # noqa: BLE001 — a fill failure leaves the order working
        log.exception("demo immediate fill failed order=%s", order.pk)


def evaluate_resting_demo_orders(account: BrokerAccount) -> int:
    """Re-check every working order on a demo account and fill the eligible
    ones. Returns the number of orders filled. Called by the poll task and
    by a manual "Sync now"."""
    resting = BrokerOrder.objects.filter(
        broker_account=account,
        status__in=(BrokerOrder.STATUS_SUBMITTED, BrokerOrder.STATUS_PARTIAL),
    )
    filled = 0
    price_cache: dict[str, Decimal | None] = {}
    for order in resting:
        sym = order.ticker.upper()
        if sym not in price_cache:
            price_cache[sym] = live_price(account, sym)
        try:
            if try_fill_demo_order(order, price=price_cache[sym]):
                filled += 1
        except Exception:  # noqa: BLE001
            log.exception("demo resting fill failed order=%s", order.pk)
    return filled
