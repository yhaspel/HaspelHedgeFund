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
    from .doctor import heal_stored_model_maps
    from .fetching import sync_tier_models

    sync = sync_tier_models()
    drift = [r for r in verification.verify_models(None) if not r.ok]
    # P13: after the sync has deactivated whatever vanished upstream, repair
    # every STORED per-agent map that still references a dead model (schedules,
    # user prefs, graph versions, queued runs) — drift now self-repairs daily
    # without a deploy instead of waiting to be re-healed on every dispatch.
    healed = heal_stored_model_maps()
    quarantined = getattr(sync, "quarantined", [])
    membership_added = getattr(sync, "membership_added", [])
    if (
        sync.deactivated
        or sync.excluded
        or sync.swept
        or quarantined
        or membership_added
        or drift
        or healed
    ):
        log.warning(
            "model-catalog reconcile: deactivated=%s excluded=%s swept=%s "
            "quarantined=%s membership_added=%s drift=%s healed=%s",
            sync.deactivated,
            [e.get("slug") for e in sync.excluded],
            sync.swept,
            quarantined,
            membership_added,
            [r.model_id for r in drift],
            [(h["store"], h["id"]) for h in healed],
        )
        _notify_operators(sync, drift, healed)
    else:
        log.info("model-catalog reconcile: no changes")
    return {
        "sync": sync.as_dict(),
        "drift": [r.as_dict() for r in drift],
        "healed": healed,
    }


@shared_task
def probe_provider_keys() -> dict:
    """Beat (weekdays 08:40 ET, Fridays 15:00 ET): one cheap authenticated call
    per distinct OpenRouter / FMP key in use, alerting the operator once per
    outage on a rejected key — before the daily council batch and the Friday
    autopilot cycles find out the hard way. Read-only; see ``key_probe``."""
    if skip_when_offline("probe_provider_keys"):
        return {"status": "skipped_offline"}
    from .key_probe import probe_all

    return probe_all()


@shared_task
def heal_stored_model_maps_task() -> list[dict]:
    """Stored-map doctor on demand — dispatched by ``quarantine.quarantine_model``
    the moment a live call proves a catalog model dead, so schedules / prefs /
    graph versions / queued runs stop referencing it today, not at the next
    daily reconcile."""
    if skip_when_offline("heal_stored_model_maps_task"):
        return []
    from .doctor import heal_stored_model_maps

    return heal_stored_model_maps()


def _notify_operators(sync, drift, healed=None) -> None:
    """Best-effort operator notification over active staff email channels. Never
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
            "Excluded: " + ", ".join(f"{e['slug']} ({e['reason']})" for e in sync.excluded)
        )
    if getattr(sync, "quarantined", None):
        lines.append(
            "Still quarantined (dead on live calls, kept inactive): " + ", ".join(sync.quarantined)
        )
    if getattr(sync, "membership_added", None):
        lines.append("Added to tier menus: " + ", ".join(sync.membership_added))
    if drift:
        lines.append("Pricing drift: " + ", ".join(r.model_id for r in drift))
    if healed:
        n = sum(len(h["moves"]) for h in healed)
        lines.append(
            f"Self-healed {n} stored model reference(s): "
            + "; ".join(
                f"{h['store']}#{h['id']} ("
                + ", ".join(f"{m['agent']}: {m['from']}→{m['to']}" for m in h["moves"])
                + ")"
                for h in healed
            )
        )
    body = "Model-catalog reconcile found changes:\n\n" + "\n".join(lines)
    try:
        channels = NotificationChannel.objects.filter(
            user__is_staff=True, is_active=True, kind=NotificationChannel.EMAIL
        )
        for ch in channels:
            send_notification(ch, "Model catalog reconcile", body, enforce_cap=False)
    except Exception:
        return
