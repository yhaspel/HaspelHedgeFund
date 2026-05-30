"""Notification channels + delivery audit (P3b).

v1 ships **email** and **telegram**. ``kind`` is a CharField (not a hard FK to
a channel-type table) so adding Slack/Discord later is a one-line choices edit
plus a new ``channels/*.py`` sender — see the deferred plan
``phase-03b-1-slack-discord-notifications.md``.

``config`` is per-kind:
* email    → ``{"address": "you@example.com"}`` (falls back to ``user.email``)
* telegram → ``{"bot_token": "...", "chat_id": "..."}`` (user-supplied; see
  ``guides/telegram-setup.md``)
"""
from __future__ import annotations

from django.conf import settings
from django.db import models


class NotificationChannel(models.Model):
    EMAIL = "email"
    TELEGRAM = "telegram"
    KIND_CHOICES = [
        (EMAIL, "Email"),
        (TELEGRAM, "Telegram"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_channels",
    )
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    name = models.CharField(max_length=120, blank=True, default="")
    config = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} channel (user={self.user_id})"

    @property
    def label(self) -> str:
        return self.name or self.get_kind_display()


class NotificationEvent(models.Model):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    THROTTLED = "throttled"
    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (SENT, "Sent"),
        (FAILED, "Failed"),
        (THROTTLED, "Throttled"),
    ]

    channel = models.ForeignKey(
        NotificationChannel, on_delete=models.CASCADE, related_name="events"
    )
    # Set when the event was produced by a scheduled-run fire. Nullable so test
    # messages (no schedule) and future ad-hoc notifications can reuse the model.
    triggered_by = models.ForeignKey(
        "schedules.ScheduledRunHistory",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="notification_events",
    )
    subject = models.CharField(max_length=255)
    body = models.TextField(blank=True, default="")
    delivery_status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=PENDING
    )
    error = models.TextField(blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.subject} [{self.delivery_status}]"
