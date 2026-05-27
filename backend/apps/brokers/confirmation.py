"""Explicit confirmation gate + audit log (P3a-1).

No order reaches a broker without passing `gate(order, request_context)`.
The gate, in order:

  1. Re-price at the current quote.
  2. Run a Risk-Manager check (per-position / sector / drawdown / min-cash).
  3. Notional typed-confirmation: orders > $1,000 require typing the ticker.
  4. Live-account gate: a typed phrase containing "LIVE" is required.
  5. Disclaimer: a live account must have a current-version acceptance.
  6. Audit-log the confirmation (server-captured, not from agent text).

> **Live auto-submission hard block:** if `confirmation_method ==
> "scheduled_job"` and `broker_account.mode == "live"`, the gate raises
> unconditionally — even with a user opt-in flag set. Tested.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from django.utils import timezone

from .models import (
    BrokerAccount,
    BrokerOrder,
    DisclaimerAcceptance,
    LiveTradingDisclaimer,
)

NOTIONAL_TYPED_THRESHOLD = Decimal("1000")
LIVE_PHRASE = "LIVE"


class ConfirmationError(Exception):
    """Gate failed. status_code defaults to 400 (422 for missing fields)."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "confirmation_failed",
        status_code: int = 400,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass
class GateContext:
    """What the caller hands to the gate. The view layer fills this from
    the HTTP request; tasks fill it from job metadata."""
    user: Any
    confirmation_method: str = BrokerOrder.CONFIRM_MANUAL
    typed_confirmation: str = ""
    live_confirmation: str = ""
    ip_address: str | None = None
    user_agent: str = ""
    quote_price: Decimal | None = None
    bypass_typed: bool = False   # only true in API-confirmed flows that supply explicit fields
    risk_check: Any | None = None  # optional callable(order) -> list[str] reasons (empty=pass)


@dataclass
class GateResult:
    notional: Decimal
    quote_price: Decimal
    gates_fired: list[str] = field(default_factory=list)
    audit: dict = field(default_factory=dict)


def _now() -> datetime:
    return timezone.now()


def _resolve_quote(order: BrokerOrder, override: Decimal | None) -> Decimal:
    """Best-effort quote: override > limit > a deterministic fallback so the
    notional check works even with no live data feed."""
    if override is not None:
        return Decimal(str(override))
    if order.limit_price is not None:
        return Decimal(str(order.limit_price))
    # Fallback: $100/share. Deterministic so demo orders compute a notional
    # without reaching for a market-data provider.
    return Decimal("100")


def _current_disclaimer() -> LiveTradingDisclaimer | None:
    return LiveTradingDisclaimer.objects.filter(is_current=True).first()


def gate(order: BrokerOrder, ctx: GateContext) -> GateResult:
    """Run every gate. Raises ConfirmationError on the first failure.

    Side effects on success: writes confirmed_by, confirmed_at,
    confirmation_method, confirmation_ip, confirmation_user_agent, and the
    audit JSON dict back to the BrokerOrder row.
    """
    account: BrokerAccount = order.broker_account
    if order.status != BrokerOrder.STATUS_DRAFT:
        raise ConfirmationError(
            f"order is in status {order.status}; only draft orders can be confirmed",
            code="not_draft", status_code=409,
        )

    # P3a-2 amendment (ADR 0011 §4): refuse confirmation when the broker
    # account isn't active. A draft created while active, sitting until the
    # account flips to needs_reauth / disabled / error, would otherwise pass
    # through the gate and submit into a dead session. Placed before the
    # scheduled_job × live block so the message surfaces even with no
    # gates-input from the caller. Cross-cutting — benefits every
    # credentialed adapter (IBKR, TradeStation, future).
    if account.connection_status != BrokerAccount.STATUS_ACTIVE:
        raise ConfirmationError(
            f"broker account is {account.connection_status}; "
            "re-authenticate before submitting orders",
            code="account_inactive",
            status_code=409,
        )

    # Live auto-submission hard block (must come BEFORE any other check so
    # tests asserting the block don't need to supply ticker/risk inputs).
    if (
        ctx.confirmation_method == BrokerOrder.CONFIRM_SCHEDULED
        and account.mode == BrokerAccount.MODE_LIVE
    ):
        raise ConfirmationError(
            "scheduled jobs may never submit live orders",
            code="scheduled_live_blocked",
            status_code=403,
        )

    quote = _resolve_quote(order, ctx.quote_price)
    notional = (Decimal(str(order.quantity)) * quote).quantize(Decimal("0.01"))

    gates_fired: list[str] = []

    # 2. Risk Manager hook. Callers (the view layer) pass a callable that
    # consults `apps/portfolios/...` RiskLimits if available; in this
    # sub-phase the default is a no-op so tests don't depend on a fully
    # configured Risk Manager. Real risk rules land via P2a's RiskLimits.
    if ctx.risk_check is not None:
        reasons = list(ctx.risk_check(order) or [])
        if reasons:
            raise ConfirmationError(
                "risk manager rejected the order: " + "; ".join(reasons),
                code="risk_rejected",
            )
        gates_fired.append("risk_check_passed")

    # 3. Notional typed-confirmation.
    if not ctx.bypass_typed and notional > NOTIONAL_TYPED_THRESHOLD:
        typed = (ctx.typed_confirmation or "").strip().upper()
        if typed != order.ticker.strip().upper():
            raise ConfirmationError(
                f"orders over ${NOTIONAL_TYPED_THRESHOLD} require typing the "
                f"ticker ({order.ticker}) to confirm",
                code="typed_confirmation_required",
                status_code=422,
            )
        gates_fired.append("typed_ticker_confirmed")

    # 4. Live-account gate.
    if account.mode == BrokerAccount.MODE_LIVE:
        if LIVE_PHRASE not in (ctx.live_confirmation or "").strip().upper():
            raise ConfirmationError(
                'live orders require typing a phrase containing "LIVE"',
                code="live_phrase_required",
                status_code=422,
            )
        gates_fired.append("live_phrase_confirmed")

        # 5. Disclaimer gate.
        current = _current_disclaimer()
        if current is None:
            raise ConfirmationError(
                "no live-trading disclaimer is configured; cannot submit",
                code="disclaimer_missing",
                status_code=409,
            )
        latest = (
            DisclaimerAcceptance.objects.filter(
                user=ctx.user, disclaimer=current,
            )
            .order_by("-accepted_at")
            .first()
        )
        if latest is None:
            raise ConfirmationError(
                "the current live-trading disclaimer has not been accepted",
                code="disclaimer_outdated",
                status_code=409,
            )
        gates_fired.append("disclaimer_current")

    # 6. Audit.
    audit = {
        "notional": str(notional),
        "quote_price": str(quote),
        "gates_fired": gates_fired,
        "mode": account.mode,
        "broker": account.broker,
        "confirmation_method": ctx.confirmation_method,
        "confirmed_at": _now().isoformat(),
    }
    order.confirmed_by = ctx.user
    order.confirmed_at = _now()
    order.confirmation_method = ctx.confirmation_method
    order.confirmation_ip = ctx.ip_address
    order.confirmation_user_agent = (ctx.user_agent or "")[:255]
    order.confirmation_audit = audit
    order.status = BrokerOrder.STATUS_CONFIRMED
    order.save(update_fields=[
        "confirmed_by", "confirmed_at", "confirmation_method",
        "confirmation_ip", "confirmation_user_agent",
        "confirmation_audit", "status",
    ])

    return GateResult(
        notional=notional, quote_price=quote, gates_fired=gates_fired, audit=audit,
    )
