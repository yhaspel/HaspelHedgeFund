"""Scheduled-run dispatcher + executor (P3b).

``dispatch_due_scheduled_runs`` is a beat task (every 60s): it finds schedules
whose ``next_run_at`` has passed, advances them, and — for market-aware
schedules on a trading day — creates one idempotent history row per fire time
and hands it to ``execute_scheduled_run``.

``execute_scheduled_run`` does the cost-ceiling check (skip/degrade/notify_only),
fans out one single-ticker Run per watchlist ticker (respecting the load-bearing
single-ticker constraint), runs materiality gating per ticker, and notifies.

The child council runs are executed **synchronously** (``execute_run(run.id)``,
not ``.delay``) so materiality + notification can run immediately after all
tickers complete — scheduled runs are best-effort, so a sequential batch in one
worker slot is acceptable and far simpler than a chord+callback.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from celery import shared_task
from django.utils import timezone

from apps.runs.models import Run
from apps.runs.tasks import execute_run

from .costs import cheaper_preset, estimate_run_cost, resolve_overrides
from .models import ScheduledRun, ScheduledRunHistory
from .triggers import compute_next, market_gate_ok

log = logging.getLogger(__name__)


@shared_task(name="apps.schedules.tasks.dispatch_due_scheduled_runs")
def dispatch_due_scheduled_runs() -> dict:
    now = timezone.now()
    due_ids = list(
        ScheduledRun.objects.filter(
            is_active=True, next_run_at__isnull=False, next_run_at__lte=now
        ).values_list("id", flat=True)
    )
    dispatched = 0
    skipped_market = 0
    for sr_id in due_ids:
        sr = ScheduledRun.objects.filter(pk=sr_id).first()
        if sr is None:
            continue
        fire_time = sr.next_run_at
        # Advance the schedule FIRST so a slow/duplicate dispatcher pass can't
        # re-select this same fire time.
        sr.last_run_at = now
        sr.next_run_at = compute_next(sr.cron_expression, sr.timezone, after=fire_time)
        sr.save(update_fields=["last_run_at", "next_run_at"])

        if not market_gate_ok(sr, fire_time):
            skipped_market += 1
            continue

        # Idempotency: one history per (schedule, fire_time). A duplicate
        # dispatch gets the existing row (created=False) and bails.
        hist, created = ScheduledRunHistory.objects.get_or_create(
            scheduled_run=sr,
            fire_time_utc=fire_time,
            defaults={"status": ScheduledRunHistory.PENDING},
        )
        if not created:
            continue
        execute_scheduled_run.delay(sr.id, hist.id)
        dispatched += 1

    return {"due": len(due_ids), "dispatched": dispatched, "skipped_market": skipped_market}


def _find_prior_run(user, ticker: str, before_run: Run):
    """Most recent prior DONE run for this user + ticker (single-ticker exact
    match — cross-DB safe, unlike a JSON ``contains`` lookup)."""
    return (
        Run.objects.filter(
            user=user,
            status=Run.DONE,
            tickers=[ticker],
            created_at__lt=before_run.created_at,
        )
        .exclude(pk=before_run.pk)
        .order_by("-created_at")
        .first()
    )


@shared_task(name="apps.schedules.tasks.execute_scheduled_run")
def execute_scheduled_run(scheduled_run_id: int, history_id: int) -> dict:
    sr = ScheduledRun.objects.select_related(
        "watchlist", "notification_channel"
    ).filter(pk=scheduled_run_id).first()
    hist = ScheduledRunHistory.objects.filter(pk=history_id).first()
    if sr is None or hist is None:
        return {"status": "missing"}

    hist.status = ScheduledRunHistory.RUNNING
    hist.save(update_fields=["status"])

    tickers = list(sr.watchlist.tickers.values_list("ticker", flat=True))
    if not tickers:
        hist.status = ScheduledRunHistory.DONE
        hist.finished_at = timezone.now()
        hist.error = "watchlist is empty"
        hist.save(update_fields=["status", "finished_at", "error"])
        return {"status": "empty"}

    # ---- cost ceiling ----
    preset = sr.model_preset
    est = estimate_run_cost(sr.user, preset, sr.model_overrides, sr.personas, len(tickers))
    ceiling = float(sr.cost_ceiling_usd) if sr.cost_ceiling_usd is not None else None
    degraded_from = ""
    overage = False

    if ceiling is not None and est["est_total_usd"] > ceiling:
        if sr.on_breach == ScheduledRun.SKIP:
            hist.status = ScheduledRunHistory.SKIPPED
            hist.estimated_cost_usd = Decimal(str(est["est_total_usd"]))
            hist.error = (
                f"cost ceiling exceeded: est ${est['est_total_usd']} > ${ceiling}"
            )
            hist.finished_at = timezone.now()
            hist.save(update_fields=["status", "estimated_cost_usd", "error", "finished_at"])
            return {"status": "skipped_cost"}
        if sr.on_breach == ScheduledRun.DEGRADE:
            cur = preset
            while est["est_total_usd"] > ceiling:
                nxt = cheaper_preset(cur)
                if nxt is None or nxt == cur:
                    break
                cur = nxt
                est = estimate_run_cost(
                    sr.user, cur, sr.model_overrides, sr.personas, len(tickers)
                )
            if cur != preset:
                degraded_from = preset
                preset = cur
            if est["est_total_usd"] > ceiling:
                hist.status = ScheduledRunHistory.SKIPPED
                hist.estimated_cost_usd = Decimal(str(est["est_total_usd"]))
                hist.degraded_preset = preset if degraded_from else ""
                hist.error = "cost ceiling exceeded even after degrading to the cheapest preset"
                hist.finished_at = timezone.now()
                hist.save(update_fields=[
                    "status", "estimated_cost_usd", "degraded_preset", "error", "finished_at",
                ])
                return {"status": "skipped_after_degrade"}
        elif sr.on_breach == ScheduledRun.NOTIFY_ONLY:
            overage = True

    overrides = resolve_overrides(sr.user, preset, sr.model_overrides)
    hist.estimated_cost_usd = Decimal(str(est["est_total_usd"]))
    hist.degraded_preset = degraded_from
    hist.save(update_fields=["estimated_cost_usd", "degraded_preset"])

    # ---- fan out: one single-ticker Run per watchlist ticker ----
    as_of = timezone.localdate()
    personas = list(sr.personas or [])
    run_ids: list[int] = []
    for tk in tickers:
        run = Run.objects.create(
            user=sr.user,
            tickers=[tk],
            model_overrides=overrides,
            as_of_date=as_of,
            personas=personas,
            source=Run.ADHOC,
        )
        run_ids.append(run.id)
        try:
            execute_run(run.id)  # synchronous — see module docstring
        except Exception:  # noqa: BLE001 — one ticker failing must not abort the batch
            log.exception("scheduled run: ticker %s failed (run=%s)", tk, run.id)
    hist.runs.set(run_ids)

    # ---- materiality + notify ----
    from apps.notifications import materiality
    from apps.notifications.content import build_content
    from apps.notifications.services import send_notification

    channel = sr.notification_channel
    summary = []
    notified = 0
    actual_cost = Decimal("0")
    for run_id in run_ids:
        run = Run.objects.filter(pk=run_id).first()
        if run is None:
            continue
        actual_cost += run.total_cost_usd or Decimal("0")
        if run.status != Run.DONE:
            summary.append({"ticker": run.tickers[0], "notify": False, "status": run.status})
            continue
        ticker = run.tickers[0]
        prior = _find_prior_run(sr.user, ticker, run)
        result = materiality.evaluate(run, prior)
        result["ticker"] = ticker
        result["run_id"] = run_id
        if overage:
            result.setdefault("reasons", []).insert(0, "cost ceiling overage (notify_only)")
            result["notify"] = True
        summary.append(result)
        if result["notify"] and channel is not None and channel.is_active:
            content = build_content(run, result)
            ev = send_notification(
                channel,
                content["subject"],
                content["text"],
                html_body=content["html"],
                triggered_by=hist,
            )
            if ev.delivery_status == ev.SENT:
                notified += 1

    hist.materiality_decision = {
        "runs": summary,
        "degraded_from": degraded_from,
        "active_preset": preset,
        "overage": overage,
    }
    hist.notified_count = notified
    hist.actual_cost_usd = actual_cost
    hist.status = ScheduledRunHistory.DONE
    hist.finished_at = timezone.now()
    hist.save(
        update_fields=[
            "materiality_decision", "notified_count", "actual_cost_usd",
            "status", "finished_at",
        ]
    )
    return {"status": "done", "tickers": len(run_ids), "notified": notified}
