from __future__ import annotations

from rest_framework import generics, permissions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ScheduledRun, ScheduledRunHistory
from .serializers import ScheduledRunHistorySerializer, ScheduledRunSerializer


class ScheduledRunListCreateView(generics.ListCreateAPIView):
    serializer_class = ScheduledRunSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return ScheduledRun.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        sr = serializer.save(user=self.request.user)
        sr.reschedule()
        sr.save(update_fields=["next_run_at"])


class ScheduledRunDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ScheduledRunSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return ScheduledRun.objects.filter(user=self.request.user)

    def perform_update(self, serializer):
        sr = serializer.save()
        # cron / timezone / is_active may have changed → recompute next fire.
        sr.reschedule()
        sr.save(update_fields=["next_run_at"])


class ScheduledRunHistoryView(generics.ListAPIView):
    serializer_class = ScheduledRunHistorySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return ScheduledRunHistory.objects.filter(
            scheduled_run__user=self.request.user,
            scheduled_run_id=self.kwargs["pk"],
        ).prefetch_related("runs")


class ScheduledRunRunNowView(APIView):
    """POST /api/scheduled-runs/<id>/run-now/ — fire the schedule immediately.

    Creates a history row stamped with the current time and dispatches the
    executor. Lets the user test a schedule without waiting for its cron.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request, pk: int) -> Response:
        from django.utils import timezone

        from .tasks import execute_scheduled_run

        try:
            sr = ScheduledRun.objects.get(pk=pk, user=request.user)
        except ScheduledRun.DoesNotExist:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)

        hist = ScheduledRunHistory.objects.create(
            scheduled_run=sr,
            fire_time_utc=timezone.now(),
            status=ScheduledRunHistory.PENDING,
        )
        execute_scheduled_run.delay(sr.id, hist.id)
        return Response(
            {"history_id": hist.id, "status": "dispatched"},
            status=status.HTTP_202_ACCEPTED,
        )
