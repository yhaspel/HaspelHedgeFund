from django.conf import settings
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
