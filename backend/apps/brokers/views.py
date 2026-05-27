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

import uuid
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
from .adapters.ibkr_gateway import IBKRGatewaySession
from .adapters.mock import seed_demo_book
from .capabilities import (
    AUTH_GATEWAY,
    AUTH_NONE,
    all_capabilities,
    get_capabilities,
)
from .confirmation import ConfirmationError, GateContext, gate
from .credentials import set_api_key_secret, zero_credential
from .demo_fills import place_demo_order
from .idempotency import IdempotencyConflict, submit_idempotent
from .interfaces import BrokerError, BrokerTransientError
from .models import (
    BrokerAccount,
    BrokerCredential,
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

        # account_id resolution by auth_kind:
        #   - AUTH_NONE (Demo): synthetic `demo-{userid}-{ts}` so two demo
        #     books can coexist.
        #   - AUTH_GATEWAY (IBKR): `pending-{uuid4}` placeholder; the
        #     activate endpoint rewrites it to the real id after the user
        #     picks one from /iserver/accounts. The `pending-` namespace
        #     guarantees the unique constraint can't collide across users
        #     or retries. See ADR 0011 §2. Any caller-supplied account_id
        #     is ignored — the real id is broker-discovered.
        #   - Other credentialed kinds (api_key / oauth*): caller may
        #     supply account_id; otherwise the connect step / OAuth
        #     callback sets it.
        if cap.auth_kind == AUTH_GATEWAY:
            account_id = f"pending-{uuid.uuid4()}"
        else:
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
        cap = get_capabilities(account.broker)
        is_demo = cap is not None and cap.auth_kind == AUTH_NONE

        positions = []
        equity_mtm = Decimal("0")
        for pos in account.portfolio.positions.all().order_by("ticker"):
            positions.append({
                "ticker": pos.ticker,
                "quantity": str(pos.quantity),
                "avg_cost": str(pos.avg_cost),
                "is_short": pos.is_short,
                "realized_pnl": str(pos.realized_pnl),
            })
            equity_mtm += pos.quantity * pos.avg_cost

        if is_demo:
            # The demo broker has no external venue — the portfolio *is*
            # its book. Report broker cash == portfolio cash so the two
            # overview KPIs never drift apart.
            demo_cash = account.portfolio.cash_balance
            broker_side = {
                "cash": str(demo_cash),
                "buying_power": str(demo_cash),
                "equity": str(demo_cash + equity_mtm),
                "currency": account.base_currency,
            }
        else:
            broker = get_broker(account)
            try:
                snapshot = broker.get_account()
            except Exception as exc:
                return Response({"detail": str(exc)}, status=502)
            broker_side = {
                "cash": str(snapshot.cash),
                "buying_power": str(snapshot.buying_power),
                "equity": str(snapshot.equity),
                "currency": snapshot.currency,
            }
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
            "broker": broker_side,
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
        # Stub for the OAuth brokers. P3a-2 removed gateway_session from
        # this branch (IBKR uses its own /api/broker-accounts/<id>/gateway/*
        # endpoints — see ADR 0011 §1); P3a-3 implements oauth2 for
        # TradeStation. Until then, every OAuth/gateway broker returns 501.
        account = BrokerAccountOverviewView._get(request.user, account_id)
        cap = get_capabilities(account.broker)
        if cap is None or cap.auth_kind not in ("oauth2", "oauth1"):
            raise ValidationError({"detail": "this broker does not use OAuth"})
        return Response({
            "authorization_url": "",
            "status": "stub",
            "detail": (
                "OAuth handshake is implemented in the broker-specific "
                "phase (3a-3 — TradeStation)."
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


# --- IBKR gateway_session connect endpoints (P3a-2, ADR 0011) ---------------
#
# These five routes drive the IBKR-specific connect-wizard step. They are
# gateway_session-specific by name (`/gateway/*`) so a future credentialed
# broker with the same shape can reuse the pattern without overloading
# generic routes. The Django-settings `IBKR_GATEWAY_BASE_URL` and
# `IBKR_GATEWAY_LOGIN_URL` describe deployment topology; per-account
# `BrokerAccount.config` stays empty in v1 (ADR 0011 §1).


def _require_ibkr_account(user, account_id: int) -> BrokerAccount:
    """Resolve an IBKR account scoped to `user`; raise 400 if it isn't IBKR."""
    account = BrokerAccountOverviewView._get(user, account_id)
    if account.broker != "ibkr":
        raise ValidationError({"detail": "this account is not IBKR"})
    return account


class IBKRRuntimeConfigView(APIView):
    """GET /api/broker-accounts/ibkr/runtime-config/ — the connect wizard
    fetches this to surface the user-browser login URL without baking the
    deployment topology into the frontend (ADR 0011 §1)."""

    def get(self, request) -> Response:
        from django.conf import settings as _settings
        return Response({
            "gateway_login_url": _settings.IBKR_GATEWAY_LOGIN_URL,
        })


class BrokerGatewayProbeView(APIView):
    """POST .../gateway/probe/ — reachability check against
    settings.IBKR_GATEWAY_BASE_URL via POST /v1/api/tickle.

    Returns 200 regardless — `reachable: false` is a legitimate state the
    wizard reacts to with "show setup instructions + Retry".
    """

    def post(self, request, account_id: int) -> Response:
        _require_ibkr_account(request.user, account_id)
        try:
            with IBKRGatewaySession() as session:
                payload = session.tickle()
        except BrokerTransientError as exc:
            return Response({"reachable": False, "detail": str(exc)})
        except BrokerError as exc:
            # 4xx from the gateway is still "reachable" — the service is
            # up, it just didn't like the call. Surface it so the user
            # knows the gateway is there but unhappy.
            return Response({"reachable": True, "detail": str(exc)})
        return Response({"reachable": True, "payload": payload})


class BrokerGatewayAuthStatusView(APIView):
    """POST .../gateway/auth-status/ — proxies POST /v1/api/iserver/auth/status.

    The frontend polls this while the user logs in via the gateway's own
    browser page. Returns 200 always so the polling client has a clean
    contract — the body carries `authenticated: bool`, `connected: bool`,
    and a derived `ready` flag.
    """

    def post(self, request, account_id: int) -> Response:
        _require_ibkr_account(request.user, account_id)
        try:
            with IBKRGatewaySession() as session:
                status_payload = session.auth_status()
        except BrokerTransientError as exc:
            return Response({
                "authenticated": False,
                "connected": False,
                "ready": False,
                "detail": str(exc),
            })
        ready = bool(status_payload.get("authenticated")) and bool(
            status_payload.get("connected"),
        )
        return Response({
            "authenticated": bool(status_payload.get("authenticated")),
            "connected": bool(status_payload.get("connected")),
            "competing": bool(status_payload.get("competing")),
            "ready": ready,
            "raw": status_payload,
        })


class BrokerGatewayDiscoverAccountsView(APIView):
    """POST .../gateway/discover-accounts/ — proxies GET /v1/api/iserver/accounts.

    Returns the list of broker account ids the gateway session can see,
    each annotated with a derived `is_paper` (DU-prefix per ADR 0011 §6 /
    Risks #6) so the wizard can render a clean picker.
    """

    def post(self, request, account_id: int) -> Response:
        _require_ibkr_account(request.user, account_id)
        try:
            with IBKRGatewaySession() as session:
                payload = session.get("/iserver/accounts") or {}
        except BrokerTransientError as exc:
            return Response({"detail": f"gateway query failed: {exc}"}, status=502)
        except BrokerError as exc:
            # 401-ish from the gateway: not authenticated yet.
            return Response(
                {"detail": f"gateway refused: {exc}"}, status=409,
            )
        ids = payload.get("accounts") if isinstance(payload, dict) else payload
        if not isinstance(ids, list):
            ids = []
        accounts = [
            {"account_id": str(aid), "is_paper": str(aid).startswith("DU")}
            for aid in ids
        ]
        selected = (
            payload.get("selectedAccount") if isinstance(payload, dict) else None
        )
        return Response({
            "accounts": accounts,
            "selected": selected,
        })


class BrokerGatewayActivateView(APIView):
    """POST .../gateway/activate/ {account_id} — atomically rewrite the
    `pending-{uuid}` placeholder to the real IBKR id, validate paper/live
    via DU-prefix, create the BrokerCredential row (no secret), flip
    connection_status to active. ADR 0011 §2 + §6.
    """

    def post(self, request, account_id: int) -> Response:
        account = _require_ibkr_account(request.user, account_id)
        if account.connection_status == BrokerAccount.STATUS_ACTIVE:
            raise ValidationError({"detail": "account is already active"})
        picked = (request.data.get("account_id") or "").strip()
        if not picked:
            raise ValidationError({"account_id": "required"})

        # DU-prefix validation (paper-vs-live discriminator). live mode is
        # structurally unreachable today because supports_live=False blocks
        # account creation, but the check is defensive — it costs nothing
        # and unblocks the live path the moment P3a-6 flips the flag.
        is_paper_id = picked.startswith("DU")
        if account.mode == BrokerAccount.MODE_PAPER and not is_paper_id:
            raise ValidationError({
                "detail": (
                    f"account {picked!r} does not look like a paper account; "
                    "IBKR paper account ids start with 'DU'"
                ),
            })
        if account.mode == BrokerAccount.MODE_LIVE and is_paper_id:
            raise ValidationError({
                "detail": (
                    f"account {picked!r} looks like a paper account but "
                    "this BrokerAccount is configured for live mode"
                ),
            })

        # Live disclaimer check — defensive (unreachable today; required
        # post-3a-6). Mirrors confirmation.gate's disclaimer logic.
        if account.mode == BrokerAccount.MODE_LIVE:
            current = LiveTradingDisclaimer.objects.filter(is_current=True).first()
            if current is None:
                raise ValidationError({
                    "detail": "no live-trading disclaimer is configured",
                })
            if not DisclaimerAcceptance.objects.filter(
                user=request.user, disclaimer=current,
            ).exists():
                raise ValidationError({
                    "detail": (
                        "accept the current live-trading disclaimer before "
                        "connecting a live account"
                    ),
                })

        # Sanity: confirm the picked id is visible to the gateway session.
        try:
            with IBKRGatewaySession() as session:
                accounts_payload = session.get("/iserver/accounts") or {}
        except BrokerTransientError as exc:
            return Response(
                {"detail": f"gateway query failed: {exc}"}, status=502,
            )
        except BrokerError as exc:
            return Response(
                {"detail": f"gateway refused: {exc}"}, status=409,
            )
        visible_ids = (
            accounts_payload.get("accounts")
            if isinstance(accounts_payload, dict)
            else accounts_payload
        ) or []
        if picked not in visible_ids:
            raise ValidationError({
                "detail": (
                    f"account {picked!r} is not in the current gateway "
                    "session — log in again or pick a different account"
                ),
            })

        # Collision: this user already has this IBKR account connected on a
        # different BrokerAccount row.
        collision = BrokerAccount.objects.filter(
            user=request.user, broker="ibkr", account_id=picked,
        ).exclude(pk=account.pk).first()
        if collision is not None:
            raise ValidationError({
                "detail": (
                    f"you already have IBKR account {picked!r} connected "
                    f"(label: {collision.label!r})"
                ),
            })

        with transaction.atomic():
            account.account_id = picked
            account.connection_status = BrokerAccount.STATUS_ACTIVE
            account.save(update_fields=["account_id", "connection_status"])
            BrokerCredential.objects.get_or_create(
                account=account,
                defaults={"auth_kind": BrokerCredential.AUTH_GATEWAY},
            )

        return Response(BrokerAccountSerializer(account).data)


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
        if order_type not in ("market", "limit", "stop"):
            raise ValidationError(
                {"order_type": "must be 'market', 'limit' or 'stop'"},
            )

        def _opt_price(field: str) -> Decimal | None:
            raw = data.get(field)
            if raw is None or str(raw).strip() == "":
                return None
            try:
                value = Decimal(str(raw))
            except Exception as exc:  # noqa: BLE001
                raise ValidationError({field: "must be a number"}) from exc
            if value <= 0:
                raise ValidationError({field: "must be positive"})
            return value

        limit_price = _opt_price("limit_price")
        stop_price = _opt_price("stop_price")
        if order_type == "limit" and limit_price is None:
            raise ValidationError({"limit_price": "required for a limit order"})
        if order_type == "stop" and stop_price is None:
            raise ValidationError({"stop_price": "required for a stop order"})
        if order_type == "market":
            limit_price = None
            stop_price = None
        time_in_force = (data.get("time_in_force") or "day").strip().lower()

        order = BrokerOrder.objects.create(
            broker_account=account,
            decision=decision,
            ticker=ticker,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            time_in_force=time_in_force,
        )

        # Demo accounts skip the draft -> confirm gate: the order is
        # submitted straight to the demo book and marketable orders fill
        # within this request. Credentialed brokers keep the draft flow.
        cap = get_capabilities(account.broker)
        if cap is not None and cap.auth_kind == AUTH_NONE:
            place_demo_order(order, user=request.user)
            order.refresh_from_db()

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
        # Drafts (never sent) and demo orders (no external venue) cancel
        # straight in the database — there is no adapter round-trip.
        cap = get_capabilities(order.broker_account.broker)
        is_demo = cap is not None and cap.auth_kind == AUTH_NONE
        if order.status == BrokerOrder.STATUS_DRAFT or is_demo:
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
