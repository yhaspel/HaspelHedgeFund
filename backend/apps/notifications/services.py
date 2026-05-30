"""Notification delivery service (P3b).

``send_notification`` records a :class:`NotificationEvent` for every attempt
(the audit trail), enforces the per-user daily cap, dispatches to the right
channel sender, and stamps the delivery status. Never raises — a delivery
failure must not fail the scheduled run that produced it.
"""
from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.utils import timezone

from .channels.email import send_email
from .channels.telegram import send_telegram
from .models import NotificationChannel, NotificationEvent

_SENDERS = {
    NotificationChannel.EMAIL: send_email,
    NotificationChannel.TELEGRAM: send_telegram,
}


def within_daily_cap(user) -> bool:
    cap = int(getattr(settings, "NOTIFICATIONS_MAX_PER_DAY", 10))
    cutoff = timezone.now() - dt.timedelta(hours=24)
    sent = NotificationEvent.objects.filter(
        channel__user=user,
        delivery_status=NotificationEvent.SENT,
        created_at__gte=cutoff,
    ).count()
    return sent < cap


def send_notification(
    channel: NotificationChannel,
    subject: str,
    body: str,
    *,
    html_body: str | None = None,
    triggered_by=None,
    enforce_cap: bool = True,
) -> NotificationEvent:
    ev = NotificationEvent.objects.create(
        channel=channel,
        triggered_by=triggered_by,
        subject=subject[:255],
        body=body,
    )

    def _finish(status: str, error: str = "") -> NotificationEvent:
        ev.delivery_status = status
        ev.error = error
        if status == NotificationEvent.SENT:
            ev.sent_at = timezone.now()
        ev.save(update_fields=["delivery_status", "error", "sent_at"])
        return ev

    if not channel.is_active:
        return _finish(NotificationEvent.FAILED, "channel inactive")
    if enforce_cap and not within_daily_cap(channel.user):
        return _finish(NotificationEvent.THROTTLED, "daily notification cap reached")
    sender = _SENDERS.get(channel.kind)
    if sender is None:
        return _finish(NotificationEvent.FAILED, f"no sender for kind {channel.kind!r}")

    ok, err = sender(channel, subject, body, html_body)
    return _finish(NotificationEvent.SENT if ok else NotificationEvent.FAILED, "" if ok else err)
