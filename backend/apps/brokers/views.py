"""HTTP API for the brokers app (P3a-1).

Routes:
  GET  /api/brokers/                              capabilities registry
  GET  /api/brokers/calendar/                     market_calendar.session_summary()
  GET  /api/broker-accounts/                      list current user's accounts
  POST /api/broker-accounts/                      create (broker, mode, label, [config])
  GET  /api/broker-accounts/<id>/overview/        cash + positions + recent fills
  POST /api/broker-accounts/<id>/credentials/     API-key/secret (api_key auth)
  POST /api/broker-accounts/<id>/oauth/start/     OAuth start (stub here)
  GET  /api/broker-accounts/oauth/callback/       OAuth callback (stub here)
  POST /api/broker-accounts/<id>/disconnect/      deactivate + zero credential
  POST /api/broker-accounts/<id>/sync/            manual reconciliation
  POST /api/broker-accounts/<id>/acknowledge-drift/ clear the drift banner

  GET  /api/broker/orders/                        list orders (filterable)
  POST /api/broker/orders/                        create a draft from a Decision
  POST /api/broker/orders/<id>/confirm/           run gate + submit
  POST /api/broker/orders/<id>/cancel/            cancel a submitted order

  GET  /api/disclaimers/current/                  current live-trading disclaimer
  POST /api/disclaimers/accept/                   record acceptance
"""
from __future__ import annotations

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.portfolios.models import Portfolio
from apps.runs.models import Decision

from . import market_calendar
from .adapters.mock import seed_demo_book
from .capabilities import (
    AUTH_NONE,
    all_capabilities,
    get_capabilities,
)
from .confirmation import ConfirmationError, GateContext, gate
from .credentials import set_api_key_secret, zero_credential
from .idempotency import IdempotencyConflict, submit_idempotent
from .models import (
    BrokerAccount,
    BrokerFill,
    BrokerOrder,
    BrokerSyncEvent,
    DisclaimerAcceptance,
    LiveTradingDisclaimer,
)
from .reconcile import (
    get_broker,
    ingest_order_fills,
    poll_open_orders_for_account,
    reconcile_account,
)
from .serializers import (
    BrokerAccountSerializer,
    BrokerFillSerializer,
    BrokerOrderSerializer,
    DisclaimerSerializer,
)


def _client_ip(request) -> str | None:
    fwd = request.META.get("HTTP_X_FORWARDED_FOR")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class BrokerRegistryView(APIView):
    """GET /api/brokers/ — list every known broker + its capabilities.

    Drives the connect wizard tile grid on the frontend.
    """

    def get(self, request) -> Response:
        caps = [c.to_dict() for c in all_capabilities()]
        return Response({"brokers": caps})


class MarketCalendarView(APIView):
    def get(self, request) -> Response:
        return Response(market_calendar.session_summary())


# --- Broker accounts ---------------------------------------------------------


class BrokerAccountListCreateView(generics.ListCreateAPIView):
    serializer_class = BrokerAccountSerializer

    def get_queryset(self):
        return BrokerAccount.objects.filter(user=self.request.user).order_by(
            "-created_at",
        )

    def create(self, request, *args, **kwargs):
        broker_code = (request.data.get("broker") or "").strip()
        mode = (request.data.get("mode") or "paper").strip()
        label = (request.data.get("label") or "").strip()
        config = request.data.get("config") or {}
        if not broker_code:
            raise ValidationError({"broker": "broker is required"})
        if not label:
            raise ValidationError({"label": "label is required"})

        cap = get_capabilities(broker_code)
        if cap is None:
            raise ValidationError({"broker": f"unknown broker code {broker_code!r}"})
        if not cap.available:
            raise ValidationError({
                "broker": f"{cap.display_name} is not yet available — {cap.description}"
            })
        if mode not in (BrokerAccount.MODE_PAPER, BrokerAccount.MODE_LIVE):
            raise ValidationError({"mode": "mode must be 'paper' or 'live'"})
        if mode == BrokerAccount.MODE_PAPER and not cap.supports_paper:
            raise ValidationError({"mode": f"{cap.display_name} has no paper mode"})
        if mode == BrokerAccount.MODE_LIVE and not cap.supports_live:
            raise ValidationError({"mode": f"{cap.display_name} has no live mode"})

        # For the Demo broker (auth_kind=none) we generate a synthetic
        # account_id so two demo books can coexist. For credentialed
        # brokers the connect step (or OAuth callback) sets the real id.
        account_id = (
            request.data.get("account_id")
            or f"demo-{request.user.id}-{int(timezone.now().timestamp())}"
        )

        try:
            with transaction.atomic():
                portfolio = Portfolio.objects.create(
                    user=request.user,
                    name=f"Broker · {cap.display_name} · {label}",
                    kind=Portfolio.KIND_BROKER,
                    cash_balance=Decimal("100000")
                    if cap.auth_kind == AUTH_NONE
                    else Decimal("0"),
                )
                account = BrokerAccount.objects.create(
                    user=request.user,
                    broker=broker_code,
                    mode=mode,
                    account_id=account_id,
                    label=label,
                    base_currency="USD",
                    config=config,
                    portfolio=portfolio,
                    connection_status=(
                        BrokerAccount.STATUS_ACTIVE
                        if cap.auth_kind == AUTH_NONE
                        else BrokerAccount.STATUS_CONNECTING
                    ),
                )
                if cap.auth_kind == AUTH_NONE:
                    seed_demo_book(account, cash=Decimal("100000"))
        except IntegrityError as exc:
            raise ValidationError({
                "detail": "an account with this broker + id already exists",
            }) from exc
        return Response(
            BrokerAccountSerializer(account).data, status=status.HTTP_201_CREATED,
        )


class BrokerAccountOverviewView(APIView):
    def get(self, request, account_id: int) -> Response:
        account = self._get(request.user, account_id)
        broker = get_broker(account)
        try:
            snapshot = broker.get_account()
        except Exception as exc:
            return Response({"detail": str(exc)}, status=502)
        positions = []
        for pos in account.portfolio.positions.all().order_by("ticker"):
            positions.append({
                "ticker": pos.ticker,
                "quantity": str(pos.quantity),
                "avg_cost": str(pos.avg_cost),
                "is_short": pos.is_short,
                "realized_pnl": str(pos.realized_pnl),
            })
        fills = list(
            BrokerFill.objects.filter(order__broker_account=account)
            .order_by("-filled_at")[:25]
        )
        last_drift_event = account.last_drift_event
        drift_pending = bool(
            last_drift_event and (
                account.drift_acknowledged_at is None
                or last_drift_event.started_at > account.drift_acknowledged_at
            )
        )
        last_event_id = last_drift_event.id if last_drift_event else None
        return Response({
            "account": BrokerAccountSerializer(account).data,
            "broker": {
                "cash": str(snapshot.cash),
                "buying_power": str(snapshot.buying_power),
                "equity": str(snapshot.equity),
                "currency": snapshot.currency,
            },
            "portfolio": {
                "cash_balance": str(account.portfolio.cash_balance),
                "positions": positions,
            },
            "recent_fills": BrokerFillSerializer(fills, many=True).data,
            "drift": {
                "pending": drift_pending,
                "last_event_id": last_event_id,
                "last_notes": last_drift_event.notes if last_drift_event else "",
            },
        })

    @staticmethod
    def _get(user, account_id: int) -> BrokerAccount:
        try:
            return BrokerAccount.objects.get(pk=account_id, user=user)
        except BrokerAccount.DoesNotExist as exc:
            raise ValidationError({"detail": "not found"}) from exc


class BrokerAccountCredentialsView(APIView):
    def post(self, request, account_id: int) -> Response:
        account = BrokerAccountOverviewView._get(request.user, account_id)
        cap = get_capabilities(account.broker)
        if cap is None or cap.auth_kind != "api_key":
            raise ValidationError({"detail": "this broker does not use api_key auth"})
        api_key = (request.data.get("api_key") or "").strip()
        api_secret = (request.data.get("api_secret") or "").strip()
        if not api_key:
            raise ValidationError({"api_key": "required"})
        set_api_key_secret(account, api_key=api_key, api_secret=api_secret)
        account.connection_status = BrokerAccount.STATUS_ACTIVE
        account.save(update_fields=["connection_status"])
        return Response(BrokerAccountSerializer(account).data)


class BrokerAccountOAuthStartView(APIView):
    def post(self, request, account_id: int) -> Response:
        # Stub in this sub-phase. 3a-2 / 3a-3 implement the real handshake.
        account = BrokerAccountOverviewView._get(request.user, account_id)
        cap = get_capabilities(account.broker)
        if cap is None or cap.auth_kind not in ("oauth2", "oauth1", "gateway_session"):
            raise ValidationError({"detail": "this broker does not use OAuth"})
        return Response({
            "authorization_url": "",
            "status": "stub",
            "detail": (
                "OAuth handshake is implemented in the broker-specific "
                "phase (3a-2 / 3a-3)."
            ),
        }, status=status.HTTP_501_NOT_IMPLEMENTED)


class BrokerAccountOAuthCallbackView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request) -> Response:
        # Reserved redirect target. 3a-2 / 3a-3 implement.
        return Response({
            "status": "stub",
            "detail": "OAuth callback is implemented in the broker-specific phase.",
        }, status=status.HTTP_501_NOT_IMPLEMENTED)


class BrokerAccountDisconnectView(APIView):
    def post(self, request, account_id: int) -> Response:
        account = BrokerAccountOverviewView._get(request.user, account_id)
        with transaction.atomic():
            zero_credential(account)
            account.connection_status = BrokerAccount.STATUS_DISABLED
            account.is_active = False
            account.save(update_fields=["connection_status", "is_active"])
        return Response(BrokerAccountSerializer(account).data)


class BrokerAccountSyncView(APIView):
    def post(self, request, account_id: int) -> Response:
        account = BrokerAccountOverviewView._get(request.user, account_id)
        event = reconcile_account(
            account, triggered_by=BrokerSyncEvent.TRIGGER_MANUAL,
        )
        return Response({
            "drift_detected": event.drift_detected,
            "ledger_entries_written": event.ledger_entries_written,
            "notes": event.notes,
            "id": event.id,
        })


class BrokerAccountAckDriftView(APIView):
    def post(self, request, account_id: int) -> Response:
        account = BrokerAccountOverviewView._get(request.user, account_id)
        account.drift_acknowledged_at = timezone.now()
        account.save(update_fields=["drift_acknowledged_at"])
        return Response({"ok": True, "acknowledged_at": account.drift_acknowledged_at})


# --- Orders -----------------------------------------------------------------


class BrokerOrderListCreateView(generics.ListCreateAPIView):
    serializer_class = BrokerOrderSerializer

    def get_queryset(self):
        qs = BrokerOrder.objects.filter(broker_account__user=self.request.user)
        account = self.request.query_params.get("account")
        if account:
            qs = qs.filter(broker_account_id=account)
        status_filter = self.request.query_params.get("status")
        if status_filter:
            statuses = [s.strip() for s in status_filter.split(",") if s.strip()]
            qs = qs.filter(status__in=statuses)
        return qs.order_by("-created_at")

    def create(self, request, *args, **kwargs):
        data = request.data
        account_id = data.get("broker_account")
        if not account_id:
            raise ValidationError({"broker_account": "required"})
        try:
            account = BrokerAccount.objects.get(pk=account_id, user=request.user)
        except BrokerAccount.DoesNotExist as exc:
            raise ValidationError({"broker_account": "not found"}) from exc
        if not account.is_active or account.connection_status != BrokerAccount.STATUS_ACTIVE:
            raise ValidationError({"broker_account": "account is not active"})

        decision = None
        decision_id = data.get("decision")
        if decision_id:
            decision = Decision.objects.filter(
                pk=decision_id, run__user=request.user,
            ).first()
            if decision is None:
                raise ValidationError({"decision": "not found"})

        ticker = (data.get("ticker") or "").strip().upper()
        side = (data.get("side") or "").strip().lower()
        if side not in ("buy", "sell"):
            raise ValidationError({"side": "must be 'buy' or 'sell'"})
        if not ticker:
            raise ValidationError({"ticker": "required"})
        try:
            quantity = Decimal(str(data.get("quantity") or "0"))
        except Exception as exc:
            raise ValidationError({"quantity": "must be a number"}) from exc
        if quantity <= 0:
            raise ValidationError({"quantity": "must be positive"})

        order_type = (data.get("order_type") or "market").strip().lower()
        limit_price = data.get("limit_price")
        if limit_price is not None and str(limit_price) != "":
            try:
                limit_price = Decimal(str(limit_price))
            except Exception as exc:
                raise ValidationError({"limit_price": "must be a number"}) from exc
        else:
            limit_price = None
        time_in_force = (data.get("time_in_force") or "day").strip().lower()

        order = BrokerOrder.objects.create(
            broker_account=account,
            decision=decision,
            ticker=ticker,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            time_in_force=time_in_force,
        )
        return Response(
            BrokerOrderSerializer(order).data, status=status.HTTP_201_CREATED,
        )


class BrokerOrderConfirmView(APIView):
    def post(self, request, order_id: int) -> Response:
        try:
            order = BrokerOrder.objects.select_related("broker_account").get(
                pk=order_id, broker_account__user=request.user,
            )
        except BrokerOrder.DoesNotExist as exc:
            raise ValidationError({"detail": "not found"}) from exc

        typed = (request.data.get("typed_confirmation") or "").strip()
        live = (request.data.get("live_confirmation") or "").strip()
        method = (
            request.data.get("confirmation_method") or BrokerOrder.CONFIRM_MANUAL
        ).strip()

        ctx = GateContext(
            user=request.user,
            confirmation_method=method,
            typed_confirmation=typed,
            live_confirmation=live,
            ip_address=_client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:255],
        )
        try:
            gate(order, ctx)
        except ConfirmationError as exc:
            return Response(
                {"detail": str(exc), "code": exc.code},
                status=exc.status_code,
            )

        broker = get_broker(order.broker_account)
        try:
            submit_idempotent(order=order, broker=broker)
        except IdempotencyConflict as exc:
            return Response({"detail": str(exc)}, status=409)
        except Exception as exc:
            order.refresh_from_db()
            return Response(
                {"detail": f"submission failed: {exc}", "order_status": order.status},
                status=502,
            )

        # Post-confirm pipeline. Inline (not Celery) so the test/dev
        # experience surfaces fills immediately:
        #   1) ingest this order's fills (handles same-tick MockBroker fills
        #      where the order status went straight to "filled" and so
        #      wouldn't appear in poll_open_orders' OPEN_STATUSES filter).
        #   2) poll any other still-open orders on the same account.
        #   3) reconcile_account squares residual drift the fill stream
        #      didn't account for.
        try:
            ingest_order_fills(order, broker)
        except Exception:  # pragma: no cover
            pass
        try:
            poll_open_orders_for_account(order.broker_account)
        except Exception:  # pragma: no cover
            pass
        try:
            reconcile_account(
                order.broker_account,
                triggered_by=BrokerSyncEvent.TRIGGER_POST_ORDER,
            )
        except Exception:  # pragma: no cover
            pass
        order.refresh_from_db()
        return Response(BrokerOrderSerializer(order).data)


class BrokerOrderCancelView(APIView):
    def post(self, request, order_id: int) -> Response:
        try:
            order = BrokerOrder.objects.get(
                pk=order_id, broker_account__user=request.user,
            )
        except BrokerOrder.DoesNotExist as exc:
            raise ValidationError({"detail": "not found"}) from exc
        cancellable = (
            order.status in BrokerOrder.OPEN_STATUSES
            or order.status == BrokerOrder.STATUS_DRAFT
        )
        if not cancellable:
            return Response({"detail": "order is not cancellable"}, status=409)
        if order.status == BrokerOrder.STATUS_DRAFT:
            order.status = BrokerOrder.STATUS_CANCELLED
            order.cancelled_at = timezone.now()
            order.save(update_fields=["status", "cancelled_at"])
            return Response(BrokerOrderSerializer(order).data)
        broker = get_broker(order.broker_account)
        try:
            broker.cancel_order(order.broker_order_id)
        except Exception as exc:
            return Response({"detail": f"broker rejected cancel: {exc}"}, status=502)
        order.status = BrokerOrder.STATUS_CANCELLED
        order.cancelled_at = timezone.now()
        order.save(update_fields=["status", "cancelled_at"])
        return Response(BrokerOrderSerializer(order).data)


# --- Disclaimer -------------------------------------------------------------


class CurrentDisclaimerView(APIView):
    def get(self, request) -> Response:
        current = LiveTradingDisclaimer.objects.filter(is_current=True).first()
        if current is None:
            return Response({"current": None})
        return Response({
            "current": DisclaimerSerializer(current, context={"user": request.user}).data,
        })


class DisclaimerAcceptView(APIView):
    def post(self, request) -> Response:
        version = (request.data.get("version") or "").strip()
        if not version:
            raise ValidationError({"version": "required"})
        try:
            d = LiveTradingDisclaimer.objects.get(version=version)
        except LiveTradingDisclaimer.DoesNotExist as exc:
            raise ValidationError({"version": "no such disclaimer"}) from exc
        acceptance, _ = DisclaimerAcceptance.objects.get_or_create(
            user=request.user, disclaimer=d,
            defaults={
                "ip_address": _client_ip(request),
                "user_agent": request.META.get("HTTP_USER_AGENT", "")[:255],
            },
        )
        return Response({
            "version": d.version,
            "accepted_at": acceptance.accepted_at.isoformat(),
        })
