"""P7 — autopilot scheduler tasks (apps/portfolios).

``dispatch_due_autopilots`` is a beat task (every 60s) mirroring
``apps.schedules.tasks.dispatch_due_scheduled_runs``: it finds enabled,
non-halted ``StrategyAutopilot`` rows whose ``next_run_at`` has passed, advances
them, applies the NYSE market gate, and — one idempotent ``AutopilotRun`` per
fire time — hands each to ``run_autopilot_cycle``. The three fund accounts run on
staggered crons, independently: one halting or erroring leaves the others firing.

``run_autopilot_cycle`` does the pre-flight (assert ``auto_run_council``, refresh
the broker book via ``reconcile_account``) then runs the council cycle. The
emission happens later in ``finalize_cycle`` → ``autopilot._finalize_target`` →
``maybe_emit_and_submit``, which resolves this in-flight run and records it.
"""
from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from apps.schedules.triggers import compute_next, market_gate_ok

from .models import AutopilotRun, StrategyAutopilot

log = logging.getLogger(__name__)


@shared_task(name="apps.portfolios.tasks_autopilot.dispatch_due_autopilots")
def dispatch_due_autopilots() -> dict:
    now = timezone.now()
    due_ids = list(
        StrategyAutopilot.objects.filter(
            is_enabled=True, next_run_at__isnull=False, next_run_at__lte=now,
        )
        .exclude(state=StrategyAutopilot.STATE_HALTED)
        .values_list("id", flat=True)
    )
    dispatched = 0
    skipped_market = 0
    for ap_id in due_ids:
        ap = StrategyAutopilot.objects.filter(pk=ap_id).first()
        if ap is None:
            continue
        fire_time = ap.next_run_at
        # Advance FIRST so a slow/duplicate dispatcher pass can't re-select it.
        ap.last_run_at = now
        ap.next_run_at = compute_next(ap.cron_expression, ap.timezone, after=fire_time)
        ap.save(update_fields=["last_run_at", "next_run_at"])

        if not market_gate_ok(ap, fire_time):
            skipped_market += 1
            continue

        # Idempotency: one run per (autopilot, fire_time). A duplicate dispatch
        # gets the existing row (created=False) and bails — the double-dispatch
        # guard on a beat restart.
        run, created = AutopilotRun.objects.get_or_create(
            autopilot=ap, fire_time_utc=fire_time,
            defaults={"status": AutopilotRun.PENDING},
        )
        if not created:
            continue
        run_autopilot_cycle.delay(run.id)
        dispatched += 1

    return {"due": len(due_ids), "dispatched": dispatched, "skipped_market": skipped_market}


@shared_task(name="apps.portfolios.tasks_autopilot.run_autopilot_cycle")
def run_autopilot_cycle(autopilot_run_id: int) -> dict:
    """Pre-flight + fire one council cycle for an autopilot. The bridge emission
    runs downstream in finalize_cycle; this task returns once the cycle is
    dispatched (or skipped)."""
    from .tasks import daily_long_short_cycle

    run = (
        AutopilotRun.objects.select_related("autopilot", "autopilot__strategy")
        .filter(pk=autopilot_run_id)
        .first()
    )
    if run is None:
        return {"error": "run not found"}
    ap = run.autopilot
    strategy = ap.strategy

    # Pre-flight guards (deterministic, trusted here — not by the API caller).
    if ap.state == StrategyAutopilot.STATE_HALTED:
        _finish(run, AutopilotRun.SKIPPED, {"skipped": "halted"})
        return {"skipped": "halted"}
    if not bool(getattr(strategy, "auto_run_council", True)):
        # §6.0: a cycle under autopilot must never park awaiting_review. Treat a
        # mis-set flag as halt-and-notify, not a silent stop.
        StrategyAutopilot.objects.filter(pk=ap.pk).update(state=StrategyAutopilot.STATE_HALTED)
        _finish(run, AutopilotRun.HALTED, {"halted": "auto_run_council disabled"})
        log.error("autopilot halted: auto_run_council off strategy=%s", strategy.pk)
        return {"halted": "auto_run_council disabled"}

    # Fresh book before deciding. Reconcile-and-repair: within-tolerance drift
    # auto-squares; beyond-tolerance halts (don't trade against an unexplained
    # book — §11).
    if ap.broker_account_id is not None:
        try:
            from apps.brokers.reconcile import reconcile_account

            event = reconcile_account(ap.broker_account, halt_beyond_tolerance=True)
            if event is not None and event.error_message == "drift_beyond_tolerance":
                StrategyAutopilot.objects.filter(pk=ap.pk).update(
                    state=StrategyAutopilot.STATE_HALTED
                )
                _finish(run, AutopilotRun.HALTED, {"halted": "drift_beyond_tolerance"})
                log.error("autopilot halted: drift beyond tolerance autopilot=%s", ap.pk)
                return {"halted": "drift_beyond_tolerance"}
        except Exception:  # noqa: BLE001 — reconcile is best-effort pre-flight
            log.exception("pre-flight reconcile failed autopilot=%s", ap.pk)

    # Drawdown circuit-breaker pre-flight: a hard halt stops the cycle here.
    from apps.portfolios import autopilot_risk

    dd = autopilot_risk.evaluate_drawdown(ap)
    ap.refresh_from_db()
    if ap.state == StrategyAutopilot.STATE_HALTED:
        _on_hard_halt(ap)
        _finish(run, AutopilotRun.HALTED, {"halted": "drawdown", "drawdown": dd})
        return {"halted": "drawdown", "drawdown": dd}

    AutopilotRun.objects.filter(pk=run.pk).update(
        status=AutopilotRun.RUNNING, guardrail_actions={"drawdown": dd},
    )
    result = daily_long_short_cycle(
        strategy.id, force=True, override_preset=ap.model_preset,
    )
    # If the cycle parked awaiting_review (should not happen — guarded above),
    # halt-and-notify rather than leave it dangling.
    if isinstance(result, dict) and result.get("status") == "awaiting_review":
        StrategyAutopilot.objects.filter(pk=ap.pk).update(state=StrategyAutopilot.STATE_HALTED)
        _finish(run, AutopilotRun.HALTED, {"halted": "cycle awaiting_review under autopilot"})
        return {"halted": "awaiting_review"}
    return {"dispatched": True, "cycle": result}


def _finish(run: AutopilotRun, status: str, decision: dict) -> None:
    run.status = status
    run.submit_decision = {**(run.submit_decision or {}), **decision}
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "submit_decision", "finished_at"])


def _on_hard_halt(ap: StrategyAutopilot, *, reason: str = "drawdown") -> None:
    """Hard-halt side effect: freeze by default; flatten to cash if opted in;
    notify on the transition."""
    if ap.flatten_on_halt:
        from apps.portfolios.autopilot import flatten_to_cash

        try:
            flatten_to_cash(ap)
        except Exception:  # noqa: BLE001
            log.exception("flatten_on_halt failed autopilot=%s", ap.pk)
    try:
        from apps.notifications.autopilot import HALT, notify_autopilot

        mode = "flattened to cash" if ap.flatten_on_halt else "frozen (positions held)"
        notify_autopilot(ap, HALT, f"HALTED ({reason}); {mode}. Un-halt to resume.")
    except Exception:  # noqa: BLE001
        pass


@shared_task(name="apps.portfolios.tasks_autopilot.guardrail_sweep")
def guardrail_sweep() -> dict:
    """Hourly: re-evaluate each enabled account's drawdown off its real-fill
    equity curve and auto-halt at the hard limit (freeze or flatten); release any
    held pending_open orders. Fund-level aggregation/halt lands in Stage C."""
    from apps.portfolios import autopilot_risk

    halted = 0
    for ap in StrategyAutopilot.objects.filter(is_enabled=True):
        try:
            dd = autopilot_risk.evaluate_drawdown(ap)
        except Exception:  # noqa: BLE001
            log.exception("guardrail drawdown eval failed autopilot=%s", ap.pk)
            continue
        ap.refresh_from_db()
        if dd.get("transition") and ap.state == StrategyAutopilot.STATE_HALTED:
            _on_hard_halt(ap)
            halted += 1

    # Fund-level aggregate drawdown halt (the firm-level cap over per-pod caps).
    fund_halts = 0
    from apps.portfolios.fund import evaluate_fund_drawdown
    from apps.portfolios.models import AutonomousFund

    for fund in AutonomousFund.objects.filter(state=AutonomousFund.STATE_ACTIVE):
        try:
            res = evaluate_fund_drawdown(fund)
            if res.get("halted"):
                fund_halts += 1
                from apps.notifications.autopilot import notify_fund

                notify_fund(fund, "aggregate drawdown breached — all accounts halted.")
        except Exception:  # noqa: BLE001
            log.exception("fund drawdown eval failed fund=%s", fund.pk)

    released = release_pending_open_orders()
    return {"halted": halted, "fund_halts": fund_halts, "released": released.get("released", 0)}


@shared_task(name="apps.portfolios.tasks_autopilot.release_pending_open_orders")
def release_pending_open_orders() -> dict:
    """Submit locally-held pending_open orders once the market opens (§6.6).
    Daily caps are evaluated HERE (at release), not at create time, so a
    Friday-close batch held over the weekend submits Monday."""
    from apps.brokers.market_calendar import is_market_open
    from apps.brokers.models import BrokerOrder
    from apps.portfolios.autopilot import submit_held_order

    if not is_market_open():
        return {"released": 0, "reason": "market closed"}
    now = timezone.now()
    held = list(
        BrokerOrder.objects.filter(
            status=BrokerOrder.STATUS_PENDING_OPEN, release_after__lte=now,
        ).select_related("broker_account")
    )
    released = 0
    for order in held:
        try:
            if submit_held_order(order):
                released += 1
        except Exception:  # noqa: BLE001 — one bad release can't block the rest
            log.exception("release failed order=%s", order.pk)
    return {"released": released, "candidates": len(held)}


def trigger_autopilot_now(autopilot: StrategyAutopilot) -> AutopilotRun:
    """Fire one cycle immediately (the `run-now` path). Creates an AutopilotRun
    keyed on now() and dispatches it — still fully autonomous downstream."""
    run = AutopilotRun.objects.create(
        autopilot=autopilot, fire_time_utc=timezone.now(),
        status=AutopilotRun.PENDING,
    )
    run_autopilot_cycle.delay(run.id)
    return run
