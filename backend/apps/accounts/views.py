from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework import generics, permissions
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import SignupSerializer, UserSerializer


class SignupView(generics.CreateAPIView):
    serializer_class = SignupSerializer
    permission_classes = [permissions.AllowAny]


class MeView(APIView):
    def get(self, request: Request) -> Response:
        return Response(UserSerializer(request.user).data)


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
