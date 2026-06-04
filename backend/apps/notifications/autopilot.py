"""P7 — autopilot notifications (shared helper).

The bridge + guardrail sweep bypass the scheduled-run notify/digest machinery
(``apps/schedules/tasks.py``), and ``apps/brokers`` imports notifications
nowhere — so this small helper is the single emit point for the 7 autopilot
transition types, over the user's email/Telegram channels (reusing the existing
per-user daily-cap throttle in ``send_notification``).

Every message carries the mandatory **"educational use only — not investment
advice"** footer (§10).
"""
from __future__ import annotations

import logging

from .services import send_notification

log = logging.getLogger(__name__)

DISCLAIMER = "— Educational use only. Not investment advice."

# The 7 transition types (§14).
CYCLE_DONE = "cycle_done"
FILL = "fill"
PARTIAL = "partial"
REJECT = "reject"
CAP_BREACH = "cap_breach"
HALT = "halt"
ACCOUNT_UNHEALTHY = "account_unhealthy"


def _channel_for(autopilot):
    """The autopilot's channel, else any active channel the owner has."""
    if autopilot.notification_channel_id and autopilot.notification_channel.is_active:
        return autopilot.notification_channel
    from .models import NotificationChannel

    return NotificationChannel.objects.filter(
        user=autopilot.strategy.user, is_active=True,
    ).first()


def notify_autopilot(autopilot, event_type: str, summary: str, *, ticker: str = "") -> bool:
    """Emit one autopilot notification. Best-effort: never raises into the
    trading loop. Returns True if an event was created."""
    try:
        channel = _channel_for(autopilot)
        if channel is None:
            return False
        name = autopilot.strategy.name
        subject = f"[Autopilot] {name}: {event_type.replace('_', ' ')}"
        body = f"{summary}\n\n{DISCLAIMER}"
        send_notification(
            channel, subject, body, ticker=ticker, triggered_by=None,
        )
        return True
    except Exception:  # noqa: BLE001 — notifications must never break the loop
        log.exception("autopilot notify failed ap=%s type=%s", autopilot.pk, event_type)
        return False


def notify_fund(fund, summary: str) -> bool:
    """Coalesced fund-level alert (one ping for a correlated cascade rather than
    10+). Uses any active channel the fund owner has."""
    try:
        from .models import NotificationChannel

        channel = NotificationChannel.objects.filter(user=fund.owner, is_active=True).first()
        if channel is None:
            return False
        send_notification(
            channel, f"[Fund] {fund.name}: {summary}",
            f"{summary}\n\n{DISCLAIMER}", triggered_by=None,
        )
        return True
    except Exception:  # noqa: BLE001
        log.exception("fund notify failed fund=%s", fund.pk)
        return False
