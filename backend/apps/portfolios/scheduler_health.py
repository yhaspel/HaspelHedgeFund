"""Wave 3 / WP P3 — is the scheduler alive, and is anything overdue?

The fund page could not tell a dead Celery beat from a quiet Friday: an armed
autopilot whose ``next_run_at`` slid three days into the past looked exactly
like one that simply had not fired yet. This endpoint makes that visible.

What it reports
---------------
``last_beat_tick``
    The most recent evidence that beat actually dispatched something, taken
    from whichever of these is real and freshest:

      * ``django_celery_beat.PeriodicTask.last_run_at`` — the DatabaseScheduler
        stamps it on every dispatch, so it is a genuine PER-TICK signal;
      * ``AutopilotRun.started_at`` — a fund dispatch actually happened;
      * ``ScheduledRunHistory.started_at`` — the ad-hoc scheduler fired.

    The source is named on the payload. Only the first is per-tick: an
    autopilot on a weekly cron produces a "tick" once a week, so staleness is
    only asserted when the freshest evidence comes from a per-tick source.

``autopilots``
    One row per ARMED autopilot with its ``next_run_at`` and
    ``overdue_by_seconds``. An overdue armed autopilot is the definitive dead
    scheduler signal, and it does not depend on any broker introspection.

``queue_depth``
    Deliberately ``null``. Reading it means talking to the broker (Redis) on
    the request path; the owner's rule for this wave is not to add that
    dependency to a page load. ``queue_depth_reason`` says so rather than
    quietly omitting the field.
"""
from __future__ import annotations

import datetime as dt
import logging

from django.utils import timezone

from .models import AutopilotRun, StrategyAutopilot

log = logging.getLogger(__name__)

# An armed autopilot is "overdue" once it is this far past its next_run_at.
# Beat resolution is a minute and the dispatcher runs a market gate, so a short
# grace keeps a healthy scheduler out of the warning state.
OVERDUE_GRACE_SECONDS = 15 * 60

# How stale a PER-TICK beat signal may get before beat is presumed dead. The
# hourly guardrail sweep means a live beat stamps something at least hourly.
BEAT_STALE_AFTER_SECONDS = 2 * 60 * 60

# Beat sources that prove a tick happened (as opposed to "a weekly cron fired").
PER_TICK_SOURCES = frozenset({"django_celery_beat.PeriodicTask.last_run_at"})


def _iso(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, dt.UTC)
    return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")


def _periodic_task_tick() -> dt.datetime | None:
    """Freshest ``PeriodicTask.last_run_at`` — beat's own dispatch stamp."""
    try:
        from django.db.models import Max
        from django_celery_beat.models import PeriodicTask

        return PeriodicTask.objects.aggregate(m=Max("last_run_at"))["m"]
    except Exception:  # noqa: BLE001 — app/table may be absent in some deploys
        log.debug("periodic task tick unavailable", exc_info=True)
        return None


def _last_beat_tick(strategy_ids: list[int], owner) -> dict:
    """``{at, source, age_seconds, per_tick}`` for the freshest real evidence."""
    from django.db.models import Max

    candidates: list[tuple[dt.datetime, str]] = []
    tick = _periodic_task_tick()
    if tick is not None:
        candidates.append((tick, "django_celery_beat.PeriodicTask.last_run_at"))
    if strategy_ids:
        run_at = AutopilotRun.objects.filter(
            autopilot__strategy_id__in=strategy_ids,
        ).aggregate(m=Max("started_at"))["m"]
        if run_at is not None:
            candidates.append((run_at, "AutopilotRun.started_at"))
    try:
        from apps.schedules.models import ScheduledRunHistory

        sched_at = ScheduledRunHistory.objects.filter(
            scheduled_run__user=owner,
        ).aggregate(m=Max("started_at"))["m"]
        if sched_at is not None:
            candidates.append((sched_at, "ScheduledRunHistory.started_at"))
    except Exception:  # noqa: BLE001
        log.debug("scheduled run history tick unavailable", exc_info=True)

    if not candidates:
        return {
            "at": None, "source": None, "age_seconds": None, "per_tick": False,
            "detail": (
                "No dispatch has ever been recorded — beat may never have run, or this "
                "fund has simply never fired."
            ),
        }
    at, source = max(candidates, key=lambda c: c[0])
    age = (timezone.now() - at).total_seconds()
    per_tick = source in PER_TICK_SOURCES
    return {
        "at": _iso(at),
        "source": source,
        "age_seconds": int(max(0.0, age)),
        "per_tick": per_tick,
        "detail": (
            "Celery beat stamps this on every dispatch." if per_tick else
            "Derived from the last real dispatch, not from a per-tick heartbeat — a "
            "weekly autopilot produces this signal once a week, so its age alone does "
            "not prove beat is dead."
        ),
    }


def _autopilot_rows(fund, now: dt.datetime) -> list[dict]:
    rows: list[dict] = []
    autopilots = (
        StrategyAutopilot.objects.filter(strategy__fund_sleeve__fund=fund)
        .select_related("strategy")
        .order_by("strategy__name")
    )
    last_runs: dict[int, dict] = {}
    for row in AutopilotRun.objects.filter(
        autopilot__in=[a.pk for a in autopilots],
    ).order_by("autopilot", "-started_at").values("autopilot", "status", "started_at"):
        # Ordered newest-first within each autopilot — keep the first seen.
        last_runs.setdefault(row["autopilot"], row)
    for ap in autopilots:
        overdue_by = None
        if ap.is_enabled and ap.next_run_at is not None:
            overdue_by = int(max(0.0, (now - ap.next_run_at).total_seconds()))
        last = last_runs.get(ap.pk)
        rows.append({
            "autopilot_id": ap.pk,
            "strategy_id": ap.strategy_id,
            "strategy_name": ap.strategy.name,
            "is_enabled": ap.is_enabled,
            "state": ap.state,
            "cron_expression": ap.cron_expression,
            "timezone": ap.timezone,
            "next_run_at": _iso(ap.next_run_at),
            "overdue_by_seconds": overdue_by,
            "overdue": bool(overdue_by is not None and overdue_by > OVERDUE_GRACE_SECONDS),
            "last_run_at": _iso(ap.last_run_at),
            "last_dispatch_at": _iso(last["started_at"]) if last else None,
            "last_dispatch_status": last["status"] if last else None,
            "armed_without_next_run": bool(ap.is_enabled and ap.next_run_at is None),
        })
    return rows


def scheduler_health(fund) -> dict:
    """Beat liveness + per-autopilot overdue accounting for one fund."""
    now = timezone.now()
    rows = _autopilot_rows(fund, now)
    strategy_ids = [r["strategy_id"] for r in rows]
    beat = _last_beat_tick(strategy_ids, fund.owner)

    armed = [r for r in rows if r["is_enabled"]]
    overdue = [r for r in armed if r["overdue"]]
    stale_tick = bool(
        beat["per_tick"]
        and beat["age_seconds"] is not None
        and beat["age_seconds"] > BEAT_STALE_AFTER_SECONDS
    )
    if overdue or stale_tick:
        beat_alive: bool | None = False
    elif beat["per_tick"]:
        beat_alive = True
    else:
        beat_alive = None

    warnings: list[str] = []
    if overdue:
        worst = max(r["overdue_by_seconds"] for r in overdue)
        warnings.append(
            f"{len(overdue)} armed autopilot(s) are past their next run — the worst by "
            f"{worst // 3600}h {(worst % 3600) // 60}m. Celery beat is not dispatching."
        )
    if stale_tick:
        warnings.append(
            f"Celery beat has not dispatched anything for {beat['age_seconds'] // 60} "
            f"minute(s) (limit {BEAT_STALE_AFTER_SECONDS // 60})."
        )
    for row in rows:
        if row["armed_without_next_run"]:
            warnings.append(
                f"{row['strategy_name']} is armed but has no next run scheduled "
                f"(cron {row['cron_expression']!r} may be invalid)."
            )
    if beat_alive is None and armed:
        warnings.append(
            "No per-tick beat heartbeat is available, so liveness is inferred only from "
            "whether an armed autopilot is overdue."
        )

    return {
        "now": _iso(now),
        "fund_id": fund.id,
        "fund_state": fund.state,
        "beat_alive": beat_alive,
        "last_beat_tick": beat,
        "beat_stale_after_seconds": BEAT_STALE_AFTER_SECONDS,
        "overdue_grace_seconds": OVERDUE_GRACE_SECONDS,
        "autopilots": rows,
        "armed_count": len(armed),
        "overdue_count": len(overdue),
        "queue_depth": None,
        "queue_depth_reason": (
            "Not collected: reading the Celery queue means a broker (Redis) round-trip on "
            "the request path, which this endpoint deliberately avoids."
        ),
        "warnings": warnings,
    }
