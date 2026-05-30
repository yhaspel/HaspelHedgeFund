from __future__ import annotations

from rest_framework import generics, permissions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import NotificationChannel
from .serializers import NotificationChannelSerializer
from .services import send_notification


class NotificationChannelListCreateView(generics.ListCreateAPIView):
    serializer_class = NotificationChannelSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return NotificationChannel.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class NotificationChannelDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = NotificationChannelSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return NotificationChannel.objects.filter(user=self.request.user)


class NotificationChannelTestView(APIView):
    """POST /api/notification-channels/<id>/test/ — send a hello message.

    Bypasses the daily cap (a test must always go through) so the user can
    verify a freshly-added channel works.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request, pk: int) -> Response:
        try:
            channel = NotificationChannel.objects.get(pk=pk, user=request.user)
        except NotificationChannel.DoesNotExist:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)

        ev = send_notification(
            channel,
            subject="[Hedge Fund] Test notification",
            body=(
                "👋 Hello from your hedge fund.\n\n"
                "This confirms your "
                f"{channel.get_kind_display()} channel is wired up correctly. "
                "Scheduled-run alerts will arrive here when something material "
                "happens."
            ),
            enforce_cap=False,
        )
        ok = ev.delivery_status == ev.SENT
        return Response(
            {"delivery_status": ev.delivery_status, "error": ev.error},
            status=status.HTTP_200_OK if ok else status.HTTP_502_BAD_GATEWAY,
        )
