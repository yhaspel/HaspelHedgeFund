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
    """Unauthenticated liveness probe for compose / load balancers / smoke tests.

    Intentionally cheap: no DB or cache touch. Use a separate readiness probe
    if/when one is needed.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes: list = []

    def get(self, request: Request) -> Response:
        return Response({"status": "ok"})
