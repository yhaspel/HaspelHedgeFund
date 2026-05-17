from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from hedgefund.celery import app as celery_app
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
        async_result = execute_run.delay(run.id)
        Run.objects.filter(pk=run.pk).update(celery_task_id=str(async_result.id or ""))


class RunDetailView(generics.RetrieveAPIView):
    serializer_class = RunDetailSerializer

    def get_queryset(self):
        return Run.objects.filter(user=self.request.user).prefetch_related(
            "messages", "decisions", "llm_calls"
        )


class RunCancelView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request, pk: int) -> Response:
        try:
            run = Run.objects.get(pk=pk, user=request.user)
        except Run.DoesNotExist:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        if run.status not in Run.ACTIVE_STATUSES:
            return Response(
                {"detail": f"run already {run.status}"},
                status=status.HTTP_409_CONFLICT,
            )
        if run.celery_task_id:
            # SIGTERM the prefork subprocess executing this task. The worker's
            # task wrapper catches the resulting exception and the Run row is
            # already marked cancelled below — the persisted state wins.
            celery_app.control.revoke(
                run.celery_task_id, terminate=True, signal="SIGTERM"
            )
        run.status = Run.CANCELLED
        run.error_message = "Cancelled by user."
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error_message", "finished_at"])
        return Response({"id": run.pk, "status": run.status})


class ModelCatalogView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response({"models": MODEL_CATALOG})
