from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.negotiation import BaseContentNegotiation
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .serializers import SignupSerializer, UserSerializer

# Credential endpoints are the only throttled surface on this API: an
# unauthenticated caller can otherwise grind /auth/login/ for passwords or mint
# unbounded token pairs. `auth` is scoped in REST_FRAMEWORK.DEFAULT_THROTTLE_RATES
# (10/min); nothing else opts in, so analysis endpoints stay unthrottled.
AUTH_THROTTLE_SCOPE = "auth"


class FailedAttemptThrottle(ScopedRateThrottle):
    """``ScopedRateThrottle`` that counts only attempts which FAILED.

    Credential grinding is what this endpoint has to stop; a *successful* login
    or refresh is not an attack, and counting it would lock a legitimate client
    (or a shared office NAT, which presents one IP) out of its own session while
    doing nothing extra against an attacker. So the bucket is filled by 4xx
    responses only — after ``auth`` failures in the window every further attempt
    from that ident is refused, whether or not the password is right.
    """

    def throttle_success(self) -> bool:
        # Don't record the attempt here: only `record_failure` — called once the
        # response status is known — decides whether it counts.
        return True

    def record_failure(self, request, view) -> None:
        self.scope = getattr(view, self.scope_attr, None)
        if not self.scope:
            return
        self.rate = self.get_rate()
        if self.rate is None:
            return
        self.num_requests, self.duration = self.parse_rate(self.rate)
        key = self.get_cache_key(request, view)
        if key is None:
            return
        now = self.timer()
        history = [h for h in self.cache.get(key, []) if h > now - self.duration]
        history.insert(0, now)
        self.cache.set(key, history, self.duration)


class _CountFailedAttemptsMixin:
    """Wire ``FailedAttemptThrottle`` up to the view's response status."""

    throttle_classes = [FailedAttemptThrottle]
    throttle_scope = AUTH_THROTTLE_SCOPE

    def finalize_response(self, request, response, *args, **kwargs):
        code = getattr(response, "status_code", 200)
        # 429 is excluded so a client that keeps hammering a locked bucket does
        # not extend its own lockout forever.
        if 400 <= code < 500 and code != 429:
            for throttle in self.get_throttles():
                if isinstance(throttle, FailedAttemptThrottle):
                    throttle.record_failure(request, self)
        return super().finalize_response(request, response, *args, **kwargs)


class ThrottledTokenObtainPairView(_CountFailedAttemptsMixin, TokenObtainPairView):
    pass


class ThrottledTokenRefreshView(_CountFailedAttemptsMixin, TokenRefreshView):
    pass


class LogoutView(_CountFailedAttemptsMixin, APIView):
    """POST /api/auth/logout/ — revoke a refresh token.

    Body: ``{"refresh": "<token>"}``. With ``BLACKLIST_AFTER_ROTATION`` the
    rotation path already revokes superseded tokens; this is the explicit
    "log me out everywhere on this device" path, and the only way to kill a
    leaked refresh token before its 7-day expiry. Idempotent: re-posting an
    already-blacklisted or malformed token still 205s so a client's logout
    never fails.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes: list = []

    def post(self, request: Request) -> Response:
        token = (request.data or {}).get("refresh") if hasattr(request, "data") else None
        if not token:
            return Response({"detail": "refresh token required"}, status=400)
        try:
            RefreshToken(token).blacklist()
        except TokenError:
            # Already blacklisted / expired / invalid — the caller's intent
            # (this token must not work) already holds.
            pass
        return Response(status=status.HTTP_205_RESET_CONTENT)


class SignupView(generics.CreateAPIView):
    serializer_class = SignupSerializer
    permission_classes = [permissions.AllowAny]
    # Plain ScopedRateThrottle here, not the failure-only variant: on signup the
    # SUCCESSES are the abuse (mass account creation), so every attempt counts.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = AUTH_THROTTLE_SCOPE

    def create(self, request, *args, **kwargs):
        # P12/D2: a single-user instance sets SIGNUP_ENABLED=0 so this endpoint
        # 403s. DRF's PermissionDenied (not Django's) so the DRF handler renders
        # the detail message consistently with every other API error.
        if not settings.SIGNUP_ENABLED:
            raise PermissionDenied("Signup is disabled on this instance.")
        return super().create(request, *args, **kwargs)


class MeView(APIView):
    def get(self, request: Request) -> Response:
        return Response(UserSerializer(request.user).data)


class _AlwaysJSONNegotiation(BaseContentNegotiation):
    """Ignore the client's Accept header and always answer JSON.

    A liveness probe opened in a browser sends ``Accept: text/html``; with the
    browsable renderer gone that is a 406 on an endpoint whose whole job is to
    answer "am I up?" in machine-readable form.
    """

    def select_parser(self, request, parsers):
        return parsers[0] if parsers else None

    def select_renderer(self, request, renderers, format_suffix=None):
        return (renderers[0], renderers[0].media_type)


class HealthView(APIView):
    """Unauthenticated liveness probe for compose / load balancers / smoke tests,
    and (P4-OFF) the client's online / L1 / L2 discriminator.

    Cheap by design: the only non-trivial field, ``llm.local_available``, reuses
    the 60 s-cached Ollama discovery — never a fresh probe per request. Host
    resolution uses ``settings.OLLAMA_HOST`` (this view is unauthenticated, so
    there is no per-user ``ProviderKey.ollama_host`` to read; per-user hosts stay
    a run-path concern).
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes: list = []
    renderer_classes = [JSONRenderer]
    content_negotiation_class = _AlwaysJSONNegotiation

    def get(self, request: Request) -> Response:
        offline = bool(getattr(settings, "OFFLINE_MODE", False))
        local_model = getattr(settings, "OFFLINE_LLM_MODEL", "qwen2.5:7b")
        local_available = False
        try:
            from apps.models_catalog.ollama_discovery import discover_ollama_models

            host = getattr(settings, "OLLAMA_HOST", "") or "http://localhost:11434"
            local_available = bool(discover_ollama_models(host))
        except Exception:  # noqa: BLE001 — probe must never 500 the health check
            local_available = False
        return Response(
            {
                "status": "ok",
                "offline_mode": offline,
                "build": (getattr(settings, "BUILD_SHA", "") or "")[:12] or None,
                "time": timezone.now().isoformat(),
                "llm": {
                    "forced_preset": "local" if offline else None,
                    "local_model": local_model,
                    "local_available": local_available,
                },
            }
        )


class IsSuperUser(permissions.BasePermission):
    """Operator-only: a true superuser, not merely staff (DRF's IsAdminUser
    checks is_staff, which is a weaker bar)."""

    def has_permission(self, request, view) -> bool:
        return bool(request.user and request.user.is_superuser)


class CostSummaryView(APIView):
    """P5-SH WS2.4: operator LLM-spend view. Instance-wide daily spend over the
    last N days (stacked by model) plus by-agent / by-model breakdowns, all
    aggregated from LLMCall rows with a known price. Superuser-only — this is an
    operator page, not per-user billing."""

    permission_classes = [IsSuperUser]

    def get(self, request: Request) -> Response:
        try:
            days = int(request.query_params.get("days", 30))
        except (TypeError, ValueError):
            days = 30
        days = max(1, min(days, 365))
        cutoff = timezone.now() - timedelta(days=days)

        from hedgefund_agents.models import LLMCall

        # Exclude the cost_usd < 0 "unknown price" sentinel from spend totals.
        base = LLMCall.objects.filter(created_at__gte=cutoff, cost_usd__gt=0)
        by_day_model = (
            base.annotate(day=TruncDate("created_at"))
            .values("day", "model")
            .annotate(cost_usd=Sum("cost_usd"))
            .order_by("day", "model")
        )
        by_agent = (
            base.values("agent_name")
            .annotate(cost_usd=Sum("cost_usd"), calls=Count("id"))
            .order_by("-cost_usd")
        )
        by_model = (
            base.values("model")
            .annotate(cost_usd=Sum("cost_usd"), calls=Count("id"))
            .order_by("-cost_usd")
        )
        total = base.aggregate(s=Sum("cost_usd"))["s"] or Decimal("0")

        return Response(
            {
                "days": days,
                "start": cutoff.date().isoformat(),
                "total_usd": str(total),
                "by_day_model": [
                    {"date": r["day"].isoformat(), "model": r["model"],
                     "cost_usd": str(r["cost_usd"])}
                    for r in by_day_model
                ],
                "by_agent": [
                    {"agent_name": r["agent_name"], "cost_usd": str(r["cost_usd"]),
                     "calls": r["calls"]}
                    for r in by_agent
                ],
                "by_model": [
                    {"model": r["model"], "cost_usd": str(r["cost_usd"]),
                     "calls": r["calls"]}
                    for r in by_model
                ],
            }
        )
