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
from apps.portfolios.quantity_policy import (
    QuantityPolicy,
    round_quantity_toward_zero,
)
from apps.runs.models import Decision

from . import market_calendar
from .adapters.ibkr_gateway import IBKRGatewaySession
from .adapters.mock import seed_demo_book
from .brackets import create_group, is_child_leg, is_group_anchor
from .capabilities import (
    AUTH_GATEWAY,
    AUTH_NONE,
    all_capabilities,
    get_capabilities,
)
from .confirmation import ConfirmationError, GateContext, gate, gate_bracket
from .credentials import set_api_key_secret, zero_credential
from .demo_fills import place_demo_order
from .idempotency import (
    IdempotencyConflict,
    submit_bracket_idempotent,
    submit_idempotent,
)
from .interfaces import BrokerAuthError, BrokerError, BrokerTransientError
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
    reconcile_account,
    run_post_confirm_pipeline,
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

        # Alpaca paper: validate the pair against GET /v2/account on the
        # paper host and discover the account number before persisting.
        # A bad key set is rejected here with a clear message rather than
        # failing later on the order path. (P3a-4 phase plan / ADR 0013.)
        discovered_account_id = ""
        if account.broker == "alpaca_paper":
            if not api_secret:
                raise ValidationError({"api_secret": "required for Alpaca"})
            from .adapters.alpaca_paper import _make_client
            try:
                client = _make_client(api_key=api_key, api_secret=api_secret)
                acct = client.get_account()
            except Exception as exc:  # noqa: BLE001
                raise ValidationError({
                    "detail": (
                        "Alpaca rejected those credentials. Double-check "
                        f"you pasted the paper-trading key pair. ({exc})"
                    ),
                }) from exc
            discovered_account_id = str(
                getattr(acct, "account_number", None) or getattr(acct, "id", "") or "",
            )

        set_api_key_secret(account, api_key=api_key, api_secret=api_secret)
        update_fields = ["connection_status"]
        if discovered_account_id and account.account_id != discovered_account_id:
            account.account_id = discovered_account_id
            update_fields.append("account_id")
        account.connection_status = BrokerAccount.STATUS_ACTIVE
        account.save(update_fields=update_fields)
        return Response(BrokerAccountSerializer(account).data)


class BrokerAccountOAuthStartView(APIView):
    """POST /api/broker-accounts/<id>/oauth/start/

    Returns the TradeStation authorize URL and stashes the PKCE
    verifier on the BrokerAccount.config so the callback can complete
    the exchange. ADR 0012.
    """

    def post(self, request, account_id: int) -> Response:
        account = BrokerAccountOverviewView._get(request.user, account_id)
        cap = get_capabilities(account.broker)
        if cap is None or cap.auth_kind not in ("oauth2", "oauth1"):
            raise ValidationError({"detail": "this broker does not use OAuth"})
        if account.broker != "tradestation":
            return Response({
                "detail": f"OAuth handshake for {account.broker!r} is not implemented",
            }, status=status.HTTP_501_NOT_IMPLEMENTED)
        from .adapters.tradestation_oauth import build_authorize_url
        try:
            authz = build_authorize_url(account=account)
        except BrokerError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        # Stash the PKCE verifier + the chosen api base url on the draft
        # account. The verifier is short-lived (STATE_MAX_AGE_SECONDS) and
        # only useful with the matching one-time auth code; storing
        # plaintext in config is acceptable for the brief callback window.
        cfg = dict(account.config or {})
        cfg["pkce_verifier"] = authz.code_verifier
        cfg["oauth_state"] = authz.state
        from .adapters.tradestation import api_base_for_mode
        cfg["api_base_url"] = api_base_for_mode(account.mode)
        account.config = cfg
        account.save(update_fields=["config"])
        return Response({
            "authorization_url": authz.url,
            "state": authz.state,
        })


class BrokerAccountOAuthCallbackView(APIView):
    """GET /api/broker-accounts/oauth/callback/?code=…&state=…

    Public endpoint — TradeStation redirects the browser here. The signed
    `state` carries the BrokerAccount + user binding so we can route the
    exchange without a session cookie.
    """

    permission_classes = [permissions.AllowAny]

    def get(self, request) -> Response:
        from datetime import timedelta

        from .adapters.tradestation_oauth import (
            exchange_code_for_tokens,
            verify_state,
        )
        code = (request.query_params.get("code") or "").strip()
        state = (request.query_params.get("state") or "").strip()
        error = (request.query_params.get("error") or "").strip()
        if error:
            return Response(
                {"status": "error", "detail": f"TradeStation returned: {error}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not code or not state:
            raise ValidationError({"detail": "missing code or state"})
        try:
            account_id_signed, _user_id = verify_state(state)
        except BrokerError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        try:
            account = BrokerAccount.objects.get(pk=account_id_signed)
        except BrokerAccount.DoesNotExist as exc:
            raise ValidationError({"detail": "account not found"}) from exc
        if (account.config or {}).get("oauth_state") != state:
            raise ValidationError({"detail": "OAuth state does not match draft"})
        verifier = (account.config or {}).get("pkce_verifier") or ""
        if not verifier:
            raise ValidationError(
                {"detail": "PKCE verifier missing — restart the connect flow"},
            )
        try:
            bundle = exchange_code_for_tokens(
                code=code, code_verifier=verifier, user=account.user,
            )
        except (BrokerError, BrokerTransientError) as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        from .credentials import set_oauth_tokens
        expires_at = timezone.now() + timedelta(seconds=bundle.expires_in or 1200)
        set_oauth_tokens(
            account,
            access_token=bundle.access_token,
            refresh_token=bundle.refresh_token,
            expires_at=expires_at,
            scopes=bundle.scopes,
            flavor=BrokerCredential.AUTH_OAUTH2,
        )
        # Clear the one-time verifier; state stays for replay-detection
        # tracing during the activation step.
        cfg = dict(account.config or {})
        cfg.pop("pkce_verifier", None)
        account.config = cfg
        account.save(update_fields=["config"])
        return Response({
            "status": "ok",
            "account_id": account.pk,
            "next": "discover",
        })


# --- TradeStation post-callback endpoints (P3a-3, ADR 0012) ----------------


def _require_tradestation_account(user, account_id: int) -> BrokerAccount:
    account = BrokerAccountOverviewView._get(user, account_id)
    if account.broker != "tradestation":
        raise ValidationError({"detail": "this account is not TradeStation"})
    return account


class TradeStationDiscoverAccountsView(APIView):
    """POST /api/broker-accounts/<id>/tradestation/discover-accounts/

    Hits the API base host stamped at OAuth start (so a paper draft can
    only see SIM accounts) and returns the list for the picker.
    """

    def post(self, request, account_id: int) -> Response:
        account = _require_tradestation_account(request.user, account_id)
        from .adapters.tradestation_oauth import refresh_if_needed
        try:
            token = refresh_if_needed(account)
        except BrokerError as exc:
            return Response({"detail": str(exc)}, status=409)
        base = (account.config or {}).get("api_base_url") or ""
        if not base:
            raise ValidationError(
                {"detail": "no api_base_url on draft — restart OAuth"},
            )
        import requests as _requests
        try:
            r = _requests.get(
                f"{base.rstrip('/')}/brokerage/accounts",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
        except _requests.RequestException as exc:
            return Response({"detail": f"TradeStation unreachable: {exc}"}, status=502)
        if r.status_code >= 400:
            return Response(
                {"detail": f"TradeStation refused: {r.status_code} {r.text[:200]}"},
                status=502 if r.status_code >= 500 else 409,
            )
        payload = r.json() if r.content else {}
        rows = payload.get("Accounts") if isinstance(payload, dict) else []
        accounts = []
        for row in rows or []:
            acct_id = str(row.get("AccountID") or row.get("Key") or "")
            if not acct_id:
                continue
            accounts.append({
                "account_id": acct_id,
                "type": row.get("AccountType") or "",
                "currency": row.get("Currency") or "USD",
                "status": row.get("Status") or "",
            })
        return Response({"accounts": accounts})


class TradeStationActivateView(APIView):
    """POST /api/broker-accounts/<id>/tradestation/activate/ {account_id}

    Validates the picked account exists in the discovered list, enforces
    paper-vs-live host alignment (defensive — the host was chosen at
    start), records the picked id, flips status to active. For live
    accounts: the LiveTradingDisclaimer must be accepted first.
    """

    def post(self, request, account_id: int) -> Response:
        account = _require_tradestation_account(request.user, account_id)
        if account.connection_status == BrokerAccount.STATUS_ACTIVE:
            raise ValidationError({"detail": "account is already active"})
        picked = (request.data.get("account_id") or "").strip()
        if not picked:
            raise ValidationError({"account_id": "required"})
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

        # Re-fetch the discovery list to ensure the picked id is real and
        # that the host is correctly stamped. Mode-vs-host already enforced
        # at OAuth start; this is defence in depth.
        from .adapters.tradestation import api_base_for_mode
        expected_base = api_base_for_mode(account.mode)
        actual_base = (account.config or {}).get("api_base_url") or ""
        if actual_base != expected_base:
            raise ValidationError({
                "detail": (
                    f"draft api_base_url {actual_base!r} does not match "
                    f"the mode {account.mode!r} — restart OAuth"
                ),
            })

        collision = BrokerAccount.objects.filter(
            user=request.user, broker="tradestation", account_id=picked,
        ).exclude(pk=account.pk).first()
        if collision is not None:
            raise ValidationError({
                "detail": (
                    f"you already have TradeStation account {picked!r} "
                    f"connected (label: {collision.label!r})"
                ),
            })

        with transaction.atomic():
            account.account_id = picked
            account.connection_status = BrokerAccount.STATUS_ACTIVE
            # Drop the one-time state now that activation is complete.
            cfg = dict(account.config or {})
            cfg.pop("oauth_state", None)
            account.config = cfg
            account.save(update_fields=["account_id", "connection_status", "config"])
        return Response(BrokerAccountSerializer(account).data)


class TradeStationRuntimeConfigView(APIView):
    """GET /api/broker-accounts/tradestation/runtime-config/

    Reports whether *this user* can start an OAuth handshake — they can
    if either a per-user `UserBrokerOAuthApp` row or a Django settings
    fallback supplies a `client_id` + `client_secret`. Never leaks the
    secret itself.
    """

    def get(self, request) -> Response:
        from django.conf import settings as _settings

        from .adapters.tradestation_oauth import resolve_app_credentials
        cid, csec, source = resolve_app_credentials(request.user)
        return Response({
            "configured": bool(cid and csec),
            "source": source,  # "user" | "env" | ""
            "redirect_uri": _settings.TRADESTATION_REDIRECT_URI,
        })


class TradeStationAppCredentialsView(APIView):
    """GET / PUT /api/broker-accounts/tradestation/app-credentials/

    Per-user developer-app credentials. GET reports presence + the
    masked client_id (so the user can confirm what's stored without
    leaking the secret). PUT upserts; empty strings clear the row so
    the resolver falls back to the env fallback.
    """

    def get(self, request) -> Response:
        from .adapters.tradestation_oauth import resolve_app_credentials
        from .models import UserBrokerOAuthApp
        row = UserBrokerOAuthApp.objects.filter(
            user=request.user, broker="tradestation",
        ).first()
        cid_user = ""
        if row and row.encrypted_client_id:
            from apps.models_catalog.crypto import decrypt
            try:
                cid_user = decrypt(row.encrypted_client_id)
            except Exception:  # noqa: BLE001
                cid_user = ""
        _cid, _csec, source = resolve_app_credentials(request.user)
        return Response({
            "has_user_credentials": bool(row and row.has_secret()),
            "client_id_masked": _mask(cid_user),
            "source": source,
        })

    def put(self, request) -> Response:
        from .adapters.tradestation_oauth import set_user_app_credentials
        client_id = (request.data.get("client_id") or "").strip()
        client_secret = (request.data.get("client_secret") or "").strip()
        # Both must be present together. To clear, pass both empty.
        if bool(client_id) != bool(client_secret):
            raise ValidationError({
                "detail": "client_id and client_secret must be set together",
            })
        set_user_app_credentials(
            request.user,
            client_id=client_id, client_secret=client_secret,
        )
        return self.get(request)


def _mask(value: str) -> str:
    """Show last 4 chars only — enough to recognise, not enough to leak."""
    v = (value or "").strip()
    if len(v) <= 4:
        return "•" * len(v)
    return "•" * (len(v) - 4) + v[-4:]


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


class BrokerAccountDeleteView(APIView):
    """DELETE /api/broker-accounts/<id>/ — remove a broker account and its
    orphan portfolio.

    Refuses to delete when the account has any in-flight orders
    (`idempotency_state` in `submit_pending` / `unknown`) — those
    represent broker-side commitments the user must resolve first
    (cancel or reconcile). Cleanly drafted / disconnected / errored
    accounts delete in one transaction along with their portfolio.
    """

    def delete(self, request, account_id: int) -> Response:
        account = BrokerAccountOverviewView._get(request.user, account_id)
        in_flight = account.orders.filter(
            idempotency_state__in=(
                BrokerOrder.IDEM_SUBMIT_PENDING,
                BrokerOrder.IDEM_UNKNOWN,
            ),
        ).exists()
        if in_flight:
            raise ValidationError({
                "detail": (
                    "this account has in-flight orders (submit_pending or "
                    "unknown). Cancel or reconcile them before deleting."
                ),
            })
        portfolio = account.portfolio
        with transaction.atomic():
            account.delete()
            # Portfolio is PROTECT'd from the now-deleted account; safe to
            # drop the orphan portfolio so the user doesn't see a stale
            # broker-kind portfolio in their list.
            portfolio.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


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


class BrokerAccountSettingsView(APIView):
    """PATCH /api/broker-accounts/<id>/settings/ — per-account order defaults.

    v1 exposes ``default_quantity_mode`` (whole|fractional). Fractional only
    makes sense for brokers whose capabilities support it; the order-create
    path enforces the broker capability regardless of this stored default.
    """

    def patch(self, request, account_id: int) -> Response:
        account = BrokerAccountOverviewView._get(request.user, account_id)
        mode = str(request.data.get("default_quantity_mode") or "").strip().lower()
        valid = {choice[0] for choice in BrokerAccount.QMODE_CHOICES}
        if mode not in valid:
            raise ValidationError(
                {"default_quantity_mode": f"must be one of {sorted(valid)}"},
            )
        account.default_quantity_mode = mode
        account.save(update_fields=["default_quantity_mode"])
        return Response(BrokerAccountSerializer(account).data)


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
            raw_quantity = Decimal(str(data.get("quantity") or "0"))
        except Exception as exc:
            raise ValidationError({"quantity": "must be a number"}) from exc
        if raw_quantity <= 0:
            raise ValidationError({"quantity": "must be positive"})

        cap = get_capabilities(account.broker)
        order_type = (data.get("order_type") or "market").strip().lower()
        order_class = (data.get("order_class") or "simple").strip().lower()
        time_in_force = (data.get("time_in_force") or "day").strip().lower()

        # Capability gate — closes the "stop silently becomes market" hole for
        # ANY broker. The broker must support the order type; an order_class
        # other than "simple" requires native bracket support.
        supported_types = (
            tuple(cap.supported_order_types) if cap else ("market", "limit", "stop")
        )
        if order_type not in supported_types:
            raise ValidationError({
                "order_type": (
                    f"{cap.display_name if cap else account.broker} does not "
                    f"support '{order_type}' orders"
                ),
            })
        if order_class not in ("simple", "bracket", "oto", "oco"):
            raise ValidationError(
                {"order_class": "must be 'simple', 'bracket', 'oto' or 'oco'"},
            )
        if order_class != "simple" and not (cap and cap.supports_bracket):
            raise ValidationError({
                "order_class": (
                    f"{cap.display_name if cap else account.broker} does not "
                    "support bracket / OTO / OCO orders"
                ),
            })
        # Advanced orders accept only day / gtc time-in-force.
        is_advanced = order_class != "simple" or order_type == "trailing_stop"
        if is_advanced and time_in_force not in ("day", "gtc"):
            raise ValidationError(
                {"time_in_force": "advanced orders accept only 'day' or 'gtc'"},
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
        trail_price = _opt_price("trail_price")
        trail_percent = _opt_price("trail_percent")
        take_profit_limit_price = _opt_price("take_profit_limit_price")
        stop_loss_stop_price = _opt_price("stop_loss_stop_price")
        stop_loss_limit_price = _opt_price("stop_loss_limit_price")

        # Whole-vs-fractional. Advanced orders (any grouped class, or a
        # trailing_stop) force whole shares — Alpaca rejects fractional for
        # those — regardless of account default / requested mode. Otherwise
        # fractional is allowed only when the caller opts in AND the broker
        # supports it (mirrors the Manual Book / enrollment quantity policy).
        broker_fractional = bool(cap and cap.supports_fractional)
        requested_mode = (str(data.get("quantity_mode") or "").strip().lower() or None)
        account_default_mode = getattr(account, "default_quantity_mode", None) or None
        if is_advanced:
            effective_mode = "whole"
        else:
            effective_mode = requested_mode or account_default_mode or "whole"
            if effective_mode == "fractional" and not broker_fractional:
                if requested_mode == "fractional":
                    raise ValidationError({
                        "quantity_mode": (
                            f"{cap.display_name if cap else account.broker} does not "
                            "support fractional shares; use whole shares"
                        ),
                    })
                effective_mode = "whole"
        policy = QuantityPolicy.from_mode(effective_mode)
        quantity = round_quantity_toward_zero(raw_quantity, policy)
        if quantity <= 0:
            raise ValidationError({
                "quantity": (
                    "rounds to zero whole shares at this size; increase the "
                    "quantity or switch to fractional shares"
                ),
            })

        # --- Grouped order: bracket / OTO / OCO ------------------------------
        if order_class != "simple":
            if order_class != "oco" and order_type not in ("market", "limit"):
                raise ValidationError(
                    {"order_type": "a bracket / OTO entry must be market or limit"},
                )
            if order_class == "bracket" and (
                take_profit_limit_price is None or stop_loss_stop_price is None
            ):
                raise ValidationError({
                    "detail": "a bracket requires both a take-profit and a stop-loss",
                })
            if order_class == "oto" and (
                (take_profit_limit_price is None) == (stop_loss_stop_price is None)
            ):
                raise ValidationError({
                    "detail": "an OTO requires exactly one protective exit",
                })
            if order_class == "oco" and (
                take_profit_limit_price is None or stop_loss_stop_price is None
            ):
                raise ValidationError({
                    "detail": "an OCO requires both a take-profit and a stop-loss",
                })
            if order_class in ("bracket", "oto") and order_type == "limit" \
                    and limit_price is None:
                raise ValidationError(
                    {"limit_price": "required for a limit entry"},
                )
            if order_class == "oco":
                entry_order_type = None
                entry_limit_price = None
            else:
                entry_order_type = order_type
                entry_limit_price = limit_price if order_type == "limit" else None
            anchor = create_group(
                account=account,
                decision=decision,
                ticker=ticker,
                side=side,
                quantity=quantity,
                time_in_force=time_in_force,
                order_class=order_class,
                entry_order_type=entry_order_type,
                entry_limit_price=entry_limit_price,
                take_profit_limit_price=take_profit_limit_price,
                stop_loss_stop_price=stop_loss_stop_price,
                stop_loss_limit_price=stop_loss_limit_price,
            )
            return Response(
                BrokerOrderSerializer(anchor).data, status=status.HTTP_201_CREATED,
            )

        # --- Simple single order ---------------------------------------------
        if order_type == "limit" and limit_price is None:
            raise ValidationError({"limit_price": "required for a limit order"})
        if order_type in ("stop", "stop_limit") and stop_price is None:
            raise ValidationError(
                {"stop_price": f"required for a {order_type} order"},
            )
        if order_type == "stop_limit" and limit_price is None:
            raise ValidationError(
                {"limit_price": "required for a stop_limit order"},
            )
        if order_type == "trailing_stop" and (
            (trail_price is None) == (trail_percent is None)
        ):
            raise ValidationError({
                "detail": "trailing_stop requires exactly one of "
                          "trail_price / trail_percent",
            })
        # Null out the fields that don't apply to the chosen type.
        if order_type not in ("limit", "stop_limit"):
            limit_price = None
        if order_type not in ("stop", "stop_limit"):
            stop_price = None
        if order_type != "trailing_stop":
            trail_price = None
            trail_percent = None

        order = BrokerOrder.objects.create(
            broker_account=account,
            decision=decision,
            ticker=ticker,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            trail_price=trail_price,
            trail_percent=trail_percent,
            time_in_force=time_in_force,
        )

        # Demo accounts skip the draft -> confirm gate: the order is
        # submitted straight to the demo book and marketable orders fill
        # within this request. Credentialed brokers keep the draft flow.
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

        # A protective child leg is confirmed and submitted only through its
        # anchor (the bracket/OTO entry or the OCO take-profit primary).
        if is_child_leg(order):
            return Response(
                {
                    "detail": "confirm the group through its entry / primary "
                              "order, not a protective leg",
                    "code": "not_group_anchor",
                },
                status=409,
            )

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

        is_group = is_group_anchor(order)
        gate_result = None

        # For a market bracket/OTO entry there is no order-carried reference
        # price, so fetch a live mark for the entry notional + max-loss display.
        # A missing FMP key / unknown ticker degrades gracefully — the gate
        # falls back to its own quote resolution and max-loss is left blank.
        if is_group and order.leg_role == BrokerOrder.LEG_ENTRY \
                and order.order_type == BrokerOrder.TYPE_MARKET:
            from apps.portfolios.valuation import get_mark
            try:
                mark = get_mark(order.ticker, user=request.user)
                if mark is not None:
                    ctx.quote_price = mark.price
            except Exception:  # noqa: BLE001 - quote feed optional for display
                pass

        try:
            if is_group:
                gate_result = gate_bracket(order, ctx)
            else:
                gate(order, ctx)
        except ConfirmationError as exc:
            return Response(
                {"detail": str(exc), "code": exc.code},
                status=exc.status_code,
            )

        broker = get_broker(order.broker_account)
        try:
            if is_group:
                submit_bracket_idempotent(anchor=order, broker=broker)
            else:
                submit_idempotent(order=order, broker=broker)
        except IdempotencyConflict as exc:
            return Response({"detail": str(exc)}, status=409)
        except BrokerAuthError as exc:
            # Credentials rejected — submit_idempotent already walked the order
            # back to confirmed/unsubmitted and flagged the account. Surface a
            # clear re-auth signal instead of a generic 502.
            order.refresh_from_db()
            return Response(
                {
                    "detail": f"account needs re-authentication: {exc}",
                    "code": "needs_reauth",
                    "order_status": order.status,
                },
                status=409,
            )
        except Exception as exc:
            order.refresh_from_db()
            return Response(
                {"detail": f"submission failed: {exc}", "order_status": order.status},
                status=502,
            )

        # Post-confirm pipeline — ingest this order's fills, poll the account's
        # other open orders, reconcile drift — atomically and best-effort. A
        # credential rejection flips the account to needs_reauth durably (see
        # run_post_confirm_pipeline); the periodic beat retries everything else.
        run_post_confirm_pipeline(order, broker)
        order.refresh_from_db()
        payload = BrokerOrderSerializer(order).data
        if gate_result is not None:
            payload = {
                **payload,
                "max_loss": (
                    str(gate_result.max_loss)
                    if gate_result.max_loss is not None else None
                ),
                "target_gain": (
                    str(gate_result.target_gain)
                    if gate_result.target_gain is not None else None
                ),
            }
        return Response(payload)


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
