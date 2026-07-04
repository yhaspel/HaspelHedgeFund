"""Celery tasks for the model catalog.

`reconcile_model_catalog` is the scheduled (daily) automation that replaces the
manual "Fetch latest OpenRouter models" + "Verify all pricing" buttons: it
refreshes the dev/frugal allowlist, broad-sweep deactivates any OpenRouter row
that vanished upstream (the stale-ghost class), and audits pricing drift on every
active OpenRouter row — recording + notifying operators when anything changed.
This is what keeps the catalog resilient to OpenRouter churn without a deploy.
"""
from __future__ import annotations

import logging

from celery import shared_task

from hedgefund.offline import skip_when_offline

log = logging.getLogger(__name__)


@shared_task
def reconcile_model_catalog() -> dict:
    if skip_when_offline("reconcile_model_catalog"):
        return {"status": "skipped_offline"}
    from . import verification
    from .fetching import sync_tier_models

    sync = sync_tier_models()
    drift = [r for r in verification.verify_models(None) if not r.ok]
    if sync.deactivated or sync.excluded or sync.swept or drift:
        log.warning(
            "model-catalog reconcile: deactivated=%s excluded=%s swept=%s drift=%s",
            sync.deactivated,
            [e.get("slug") for e in sync.excluded],
            sync.swept,
            [r.model_id for r in drift],
        )
        _notify_operators(sync, drift)
    else:
        log.info("model-catalog reconcile: no changes")
    return {"sync": sync.as_dict(), "drift": [r.as_dict() for r in drift]}


def _notify_operators(sync, drift) -> None:
    """Best-effort operator notification over any active staff channel. Never
    raises — the log.warning above is the durable record."""
    try:
        from apps.notifications.models import NotificationChannel
        from apps.notifications.services import send_notification
    except Exception:
        return
    lines = []
    if sync.deactivated:
        lines.append("Deactivated curated slug(s): " + ", ".join(sync.deactivated))
    if sync.swept:
        lines.append("Retired (vanished upstream): " + ", ".join(sync.swept))
    if sync.excluded:
        lines.append(
            "Excluded: "
            + ", ".join(f"{e['slug']} ({e['reason']})" for e in sync.excluded)
        )
    if drift:
        lines.append("Pricing drift: " + ", ".join(r.model_id for r in drift))
    body = "Model-catalog reconcile found changes:\n\n" + "\n".join(lines)
    try:
        channels = NotificationChannel.objects.filter(
            user__is_staff=True, is_active=True
        )
        for ch in channels:
            send_notification(
                ch, "Model catalog reconcile", body, enforce_cap=False
            )
    except Exception:
        return
