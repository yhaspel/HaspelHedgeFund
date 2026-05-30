"""Paper auto-submit for scheduled runs (P3b).

Turns each scheduled-run ``Decision`` into a PAPER ``BrokerOrder`` on the
schedule's configured account, then — unless the schedule is in draft-only mode —
confirms it via the ``scheduled_job`` gate (which HARD-BLOCKS live accounts) and
submits it. Everything here is best-effort: a failure on one ticker is recorded
and the run continues; live accounts can never be reached.

Caps (per schedule, over a trailing 24h): ``max_orders_per_day`` and
``max_notional_per_day_usd`` — a breach skips the remaining orders with an audit
note. The global ``PAPER_AUTO_SUBMIT_ENABLED`` setting + the per-schedule
``auto_paper_submit`` toggle are the kill switches.
"""
from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from apps.brokers.models import BrokerAccount, BrokerOrder

log = logging.getLogger(__name__)

# Decision.action → broker order side. Holds (and anything unmapped) are skipped.
_ACTION_SIDE = {
    "buy": "buy",
    "sell": "sell",
    "open_short": "sell",
    "cover_short": "buy",
}
# Mirrors the confirmation gate's deterministic quote fallback so sizing/notional
# work even with no market-data feed.
_FALLBACK_PRICE = Decimal("100")


def _last_close(user, ticker: str) -> Decimal:
    try:
        from apps.data.providers.factory import get_fmp_provider

        provider = get_fmp_provider(user=user)
        today = timezone.localdate()
        bars = provider.get_daily_bars(
            ticker, start=today - dt.timedelta(days=14), end=today, as_of=today
        )
        if bars:
            return Decimal(str(bars[-1].close))
    except Exception:  # noqa: BLE001 — pricing is best-effort
        log.exception("auto-submit price lookup failed for %s", ticker)
    return _FALLBACK_PRICE


def _nav(account: BrokerAccount) -> Decimal:
    pf = getattr(account, "portfolio", None)
    return Decimal(str(getattr(pf, "cash_balance", 0) or 0)) if pf else Decimal("0")


def _order_quantity(decision, account: BrokerAccount, price: Decimal) -> Decimal:
    """Shares for this order: the PM's ``target_quantity`` if it sized one, else
    ``target_weight_pct`` × account NAV ÷ price (whole shares). Zero ⇒ skip."""
    q = Decimal(str(decision.target_quantity or 0))
    if q > 0:
        return q
    weight = Decimal(str(decision.target_weight_pct or 0))
    if weight > 0 and price > 0:
        notional = _nav(account) * weight / Decimal("100")
        return (notional / price).quantize(Decimal("1"))
    return Decimal("0")


def _prior_24h(scheduled_run) -> tuple[int, Decimal]:
    """(order count, approx notional) submitted via this schedule in the last 24h —
    the basis for the daily caps across multiple fires in a day."""
    cutoff = timezone.now() - dt.timedelta(hours=24)
    prior = BrokerOrder.objects.filter(
        scheduled_histories__scheduled_run=scheduled_run, created_at__gte=cutoff
    ).distinct()
    notional = sum(
        (
            Decimal(str(o.quantity)) * (o.avg_fill_price or o.limit_price or _FALLBACK_PRICE)
            for o in prior
        ),
        Decimal("0"),
    )
    return prior.count(), notional


def auto_submit_orders(scheduled_run, hist, run_ids) -> dict:
    """Create + (fill | draft) paper orders for one scheduled fire. Returns an
    audit summary; the caller stores it on ``ScheduledRunHistory.submit_decision``.
    """
    from apps.runs.models import Run

    if not getattr(settings, "PAPER_AUTO_SUBMIT_ENABLED", True):
        return {"enabled": False, "reason": "globally disabled"}
    if not scheduled_run.auto_paper_submit:
        return {"enabled": False}

    account = scheduled_run.auto_submit_broker_account
    if account is None:
        return {"enabled": True, "skipped_all": "no broker account configured"}
    if account.mode != BrokerAccount.MODE_PAPER:
        return {"enabled": True, "skipped_all": "auto-submit is paper-only"}
    if not account.is_active or account.connection_status != BrokerAccount.STATUS_ACTIVE:
        return {"enabled": True, "skipped_all": f"account {account.connection_status}"}

    draft_only = bool(scheduled_run.auto_submit_draft_only)
    max_orders = int(scheduled_run.max_orders_per_day or 0)
    max_notional = Decimal(str(scheduled_run.max_notional_per_day_usd or 0))
    n_today, notional_today = _prior_24h(scheduled_run)

    placed: list[BrokerOrder] = []
    items: list[dict] = []
    for run_id in run_ids:
        run = Run.objects.filter(pk=run_id, status=Run.DONE).first()
        if run is None:
            continue
        decision = run.decisions.first()
        if decision is None:
            continue
        side = _ACTION_SIDE.get((decision.action or "").lower())
        if side is None:
            items.append({"ticker": decision.ticker, "skipped": f"action={decision.action}"})
            continue
        price = _last_close(scheduled_run.user, decision.ticker)
        qty = _order_quantity(decision, account, price)
        if qty <= 0:
            items.append({"ticker": decision.ticker, "skipped": "no size"})
            continue
        order_notional = qty * price
        if max_orders and n_today >= max_orders:
            items.append({"ticker": decision.ticker, "skipped": "daily order cap"})
            continue
        if max_notional and (notional_today + order_notional) > max_notional:
            items.append({"ticker": decision.ticker, "skipped": "daily notional cap"})
            continue

        try:
            order = _create_and_submit(
                account, decision, side, qty, scheduled_run.user, draft_only
            )
        except Exception as exc:  # noqa: BLE001 — one bad ticker can't fail the run
            log.exception("auto-submit failed for %s", decision.ticker)
            items.append({"ticker": decision.ticker, "skipped": f"error: {exc}"[:200]})
            continue
        placed.append(order)
        n_today += 1
        notional_today += order_notional
        items.append({
            "ticker": decision.ticker, "side": side, "quantity": str(qty),
            "order_id": order.id, "status": order.status,
        })

    if placed:
        hist.broker_orders.add(*placed)
    return {
        "enabled": True,
        "mode": "draft" if draft_only else "fill",
        "account": account.id,
        "submitted": len(placed),
        "items": items,
    }


def _create_and_submit(account, decision, side, quantity, user, draft_only) -> BrokerOrder:
    from apps.brokers.capabilities import AUTH_NONE, get_capabilities

    order = BrokerOrder.objects.create(
        broker_account=account, decision=decision, ticker=decision.ticker.upper(),
        side=side, quantity=quantity, order_type="market",
    )
    if draft_only:
        return order  # left in draft for manual confirm in the UI

    cap = get_capabilities(account.broker)
    if cap is not None and cap.auth_kind == AUTH_NONE:
        # Demo book: fills synthetically; stamp scheduled-job provenance for audit.
        from apps.brokers.demo_fills import place_demo_order

        place_demo_order(order, user=user)
        BrokerOrder.objects.filter(pk=order.pk).update(
            confirmation_method=BrokerOrder.CONFIRM_SCHEDULED
        )
        order.refresh_from_db()
    else:
        # Credentialed paper: confirm via the scheduled_job gate (live hard-blocked)
        # then submit idempotently to the paper broker.
        from apps.brokers.confirmation import GateContext, gate
        from apps.brokers.idempotency import submit_idempotent
        from apps.brokers.reconcile import get_broker

        gate(order, GateContext(
            user=user,
            confirmation_method=BrokerOrder.CONFIRM_SCHEDULED,
            bypass_typed=True,
        ))
        submit_idempotent(order=order, broker=get_broker(account))
        order.refresh_from_db()
    return order
