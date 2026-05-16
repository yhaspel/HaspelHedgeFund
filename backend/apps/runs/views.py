from rest_framework import generics, permissions
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from hedgefund_agents.registry import MODEL_CATALOG

from .models import Run
from .serializers import (
    RunCreateSerializer,
    RunDetailSerializer,
    RunListSerializer,
)
from .tasks import execute_run


class RunListCreateView(generics.ListCreateAPIView):
    def get_queryset(self):
        return Run.objects.filter(user=self.request.user).order_by("-created_at")

    def get_serializer_class(self):
        return RunCreateSerializer if self.request.method == "POST" else RunListSerializer

    def perform_create(self, serializer: RunCreateSerializer) -> None:
        run = serializer.save(user=self.request.user)
        execute_run.delay(run.id)


class RunDetailView(generics.RetrieveAPIView):
    serializer_class = RunDetailSerializer

    def get_queryset(self):
        return Run.objects.filter(user=self.request.user).prefetch_related(
            "messages", "decisions", "llm_calls"
        )


class ModelCatalogView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response({"models": MODEL_CATALOG})
