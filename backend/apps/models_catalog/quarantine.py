"""Runtime model quarantine — the feedback path from a dead LLM call into the
catalog.

The daily ``reconcile_model_catalog`` retires a model only when it VANISHES from
OpenRouter's ``/api/v1/models``. Run 1178 (2026-10-20) showed the gap: Nvidia
deleted the function behind ``nvidia/nemotron-3-super-120b-a12b:free`` while
OpenRouter kept the slug listed, so the catalog kept it active, every preset /
stored map kept picking it, and every call 404'd. The adapter's runtime fallback
is the last net for the call in flight; THIS module makes the failure stick so
the next dispatch never picks the dead route again:

* ``quarantine_model`` — called by the OpenRouter adapter on HTTP 402/404 (the
  non-transient, model-specific statuses; 401/403 are key problems, not model
  problems). Deactivates the ``ModelEntry`` with an audited note + stamp.
  Selection-time healing (``tier_menus.heal_overrides``) then moves every live
  pick off it, and the DB tier menu drops it, within the same process.
* ``is_quarantined`` — honoured by ``sync_tier_models`` so the presence-only
  catalog check does NOT resurrect a quarantined row while the TTL
  (``settings.LLM_RUNTIME_QUARANTINE_DAYS``, default 3) is running. After the
  TTL the sync may re-activate it; if it is still dead the next call hops and
  re-quarantines at once, so a flapping route costs one extra fallback hop per
  TTL window, never a failed run.

Best-effort by design: nothing here raises into an LLM call.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

log = logging.getLogger(__name__)

QUARANTINE_PREFIX = "runtime quarantine:"
QUARANTINE_STATUSES = frozenset({402, 404})
_PROVIDER_PREFIXES = ("openrouter:", "anthropic:", "ollama:")


def quarantine_days() -> int:
    try:
        return max(0, int(getattr(settings, "LLM_RUNTIME_QUARANTINE_DAYS", 3)))
    except (TypeError, ValueError):
        return 3


def _candidate_ids(model: str) -> set[str]:
    """``ModelEntry`` ids a bare or provider-qualified ``model`` may be stored as."""
    ids = {model}
    if model.startswith(_PROVIDER_PREFIXES):
        ids.add(model.split(":", 1)[-1])
    else:
        ids.add(f"openrouter:{model}")
    return ids


def is_quarantined(row, *, now: datetime | None = None) -> bool:
    """True while ``row`` carries a runtime-quarantine note younger than the TTL
    and is still inactive. An operator who flips it back on, or a lapsed TTL,
    ends the quarantine."""
    if row is None or row.is_active:
        return False
    note = (getattr(row, "last_verified_note", "") or "").strip()
    if not note.startswith(QUARANTINE_PREFIX):
        return False
    stamp = getattr(row, "last_verified_at", None)
    if stamp is None:
        return False
    now = now or timezone.now()
    return now - stamp < timedelta(days=quarantine_days())


def quarantine_model(model: str, *, status_code: int, body: str = "") -> bool:
    """Deactivate the catalog row for ``model`` after a live HTTP ``status_code``
    in ``QUARANTINE_STATUSES``. Returns True when a row was flipped inactive;
    False when there was nothing to do (not a quarantine status, no row, already
    inactive, or any DB failure — logged, never raised)."""
    if status_code not in QUARANTINE_STATUSES:
        return False
    try:
        from .models import ModelEntry

        now = timezone.now()
        note = (
            f"{QUARANTINE_PREFIX} HTTP {status_code} on a live call "
            f"{now.date().isoformat()}: {(body or '')[:160]}"
        )
        flipped = ModelEntry.objects.filter(id__in=_candidate_ids(model), is_active=True).update(
            is_active=False, last_verified_at=now, last_verified_note=note
        )
    except Exception:  # pragma: no cover — DB down / apps layer unavailable
        log.warning(
            "model quarantine: could not record %s (HTTP %s)", model, status_code, exc_info=True
        )
        return False
    if not flipped:
        return False
    log.error(
        "model quarantine: %s deactivated after HTTP %s on a live call — stored "
        "picks heal to a same-tier model on their next dispatch; the catalog sync "
        "will not resurrect it for %d day(s)",
        model,
        status_code,
        quarantine_days(),
    )
    _schedule_stored_map_doctor()
    return True


def _schedule_stored_map_doctor() -> None:
    """Best-effort: repair stored per-agent maps (schedules, prefs, graph
    versions, queued runs) now rather than at the next daily reconcile."""
    try:
        from .tasks import heal_stored_model_maps_task

        heal_stored_model_maps_task.delay()
    except Exception:  # broker unavailable / eager-mode failure — the daily run covers it
        log.info("model quarantine: stored-map doctor not scheduled", exc_info=True)
