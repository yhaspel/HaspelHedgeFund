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
from hedgefund.offline import skip_when_offline

from .models import AutopilotRun, StrategyAutopilot

log = logging.getLogger(__name__)


@shared_task(name="apps.portfolios.tasks_autopilot.dispatch_due_autopilots")
def dispatch_due_autopilots() -> dict:
    # P4-OFF: the autopilot submits orders + reconciles NAV against the broker
    # (external). Pause the whole subsystem offline — outward-facing beats no-op;
    # manual analysis cycles still run locally.
    if skip_when_offline("dispatch_due_autopilots"):
        return {"status": "skipped_offline"}
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
    skipped_fund_halt = 0
    for ap_id in due_ids:
        ap = StrategyAutopilot.objects.select_related("strategy").filter(pk=ap_id).first()
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

        # The fund kill switch is firm-wide and latched: while fund.state is
        # HALTED no member fires, whatever its own state says (an hourly sweep
        # or a per-member Resume must not be an exit). The fire is still
        # recorded, so the history shows WHY nothing ran.
        from . import sleeves

        if sleeves.fund_halted(ap.strategy):
            _finish(run, AutopilotRun.SKIPPED, {
                "skipped": "fund halted",
                "message": "the fund kill switch is on — POST /api/fund/resume/ clears it.",
            })
            skipped_fund_halt += 1
            continue

        run_autopilot_cycle.delay(run.id)
        dispatched += 1

    return {
        "due": len(due_ids),
        "dispatched": dispatched,
        "skipped_market": skipped_market,
        "skipped_fund_halt": skipped_fund_halt,
    }


@shared_task(name="apps.portfolios.tasks_autopilot.run_autopilot_cycle")
def run_autopilot_cycle(autopilot_run_id: int) -> dict:
    """Pre-flight + fire one council cycle for an autopilot. The bridge emission
    runs downstream in finalize_cycle; this task returns once the cycle is
    dispatched (or skipped)."""
    if skip_when_offline("run_autopilot_cycle"):
        return {"status": "skipped_offline"}
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
    from . import sleeves

    if sleeves.fund_halted(strategy):
        _finish(run, AutopilotRun.SKIPPED, {
            "skipped": "fund halted",
            "message": "the fund kill switch is on — POST /api/fund/resume/ clears it.",
        })
        return {"skipped": "fund halted"}
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
    # P10 §A1: a deterministic pod refuses to trade on stale or dividend-corrupt
    # market data (StaleMarketDataError). Skip-and-notify rather than size the
    # book off a bad tail bar — the fund just holds its current positions.
    from apps.data.freshness import StaleMarketDataError

    try:
        result = daily_long_short_cycle(
            strategy.id, force=True, override_preset=ap.model_preset,
        )
    except StaleMarketDataError as exc:
        from apps.notifications.autopilot import ACCOUNT_UNHEALTHY, notify_autopilot

        _finish(run, AutopilotRun.SKIPPED, {"skipped": "stale_data", "detail": str(exc)[:500]})
        log.error("autopilot skipped: stale/corrupt market data autopilot=%s: %s", ap.pk, exc)
        notify_autopilot(
            ap, ACCOUNT_UNHEALTHY,
            f"Cycle skipped — stale/corrupt market data; book held. {exc}",
        )
        return {"skipped": "stale_data", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 — audit it, alert, then re-raise
        # Anything else (FMP 429, an empty universe, a constructor bug) used to
        # propagate straight out of the task, leaving this AutopilotRun stuck in
        # RUNNING with an empty error forever and the cycle's PortfolioTarget
        # mid-flight. Record the failure on both rows and page the operator; the
        # exception still propagates so Celery records the traceback.
        _fail_cycle(run, ap, strategy, exc)
        raise
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


def _fail_cycle(run: AutopilotRun, ap: StrategyAutopilot, strategy, exc: BaseException) -> None:
    """Audit an unhandled cycle exception: the AutopilotRun and any in-flight
    PortfolioTarget go to ``failed`` with the error text, and the operator is
    paged. Best-effort — it must never mask the original exception."""
    from .models import PortfolioTarget

    message = f"{type(exc).__name__}: {exc}"[:2000]
    log.exception("autopilot cycle failed autopilot=%s strategy=%s", ap.pk, strategy.pk)
    try:
        PortfolioTarget.objects.filter(
            strategy=strategy, status__in=tuple(PortfolioTarget.ACTIVE_STATUSES),
        ).update(
            status=PortfolioTarget.FAILED,
            error_message=message[:500],
            finished_at=timezone.now(),
        )
    except Exception:  # noqa: BLE001
        log.exception("failed to mark cycle target failed autopilot=%s", ap.pk)
    try:
        run.status = AutopilotRun.FAILED
        run.error = message
        run.submit_decision = {**(run.submit_decision or {}), "failed": message[:500]}
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error", "submit_decision", "finished_at"])
    except Exception:  # noqa: BLE001
        log.exception("failed to mark autopilot run failed run=%s", run.pk)
    try:
        from apps.notifications.autopilot import ACCOUNT_UNHEALTHY, notify_autopilot

        notify_autopilot(
            ap, ACCOUNT_UNHEALTHY,
            f"Cycle FAILED — no orders emitted; the book is held. {message[:400]}",
        )
    except Exception:  # noqa: BLE001
        pass


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
    if skip_when_offline("guardrail_sweep"):
        return {"status": "skipped_offline"}
    from apps.portfolios import autopilot_risk
    from apps.portfolios.snapshots import record_snapshot

    halted = 0
    snapshots = 0
    for ap in StrategyAutopilot.objects.filter(is_enabled=True).select_related(
        "broker_account__portfolio", "strategy",
    ):
        try:
            dd = autopilot_risk.evaluate_drawdown(ap)
        except Exception:  # noqa: BLE001
            log.exception("guardrail drawdown eval failed autopilot=%s", ap.pk)
            continue
        # P10 §C2: persist the marked equity this sweep just computed — it used
        # to be discarded, which is why no NAV history existed anywhere. One
        # row per (portfolio, day); the hourly upsert converges on the
        # post-close mark. record_snapshot never raises. P14: the book is the
        # member's sleeve on the shared account (its own equity curve).
        equity = dd.get("equity") or dd.get("seeded_peak")
        pf = autopilot_risk.member_book(ap)
        if equity is not None and pf is not None:
            if record_snapshot(pf, equity=equity, cash=pf.cash_balance) is not None:
                snapshots += 1
        ap.refresh_from_db()
        # Wave 3: a transition FOUND BY THE SWEEP had no record anywhere (only a
        # mutated StrategyAutopilot.state). A dispatch-time transition already
        # lands on AutopilotRun.guardrail_actions; this covers the swept ones so
        # the fund activity feed can show them. Best-effort, never blocking.
        if dd.get("transition"):
            try:
                from apps.portfolios.fund_activity import record_guardrail_transition

                record_guardrail_transition(ap, dd)
            except Exception:  # noqa: BLE001 — audit never breaks the sweep
                log.exception("guardrail transition audit failed autopilot=%s", ap.pk)
        if dd.get("transition") and ap.state == StrategyAutopilot.STATE_HALTED:
            _on_hard_halt(ap)
            halted += 1

    # Fund-level aggregate drawdown halt (the firm-level cap over per-pod caps).
    fund_halts = 0
    from apps.portfolios.fund import evaluate_fund_drawdown
    from apps.portfolios.models import AutonomousFund

    for fund in AutonomousFund.objects.select_related("broker_account__portfolio"):
        # P14: the fund's own NAV history = the shared account's book (the truth
        # the aggregate chart and the fund breaker read) — snapshot it hourly
        # too, halted or not.
        acct_pf = fund.broker_account.portfolio if fund.broker_account_id else None
        if acct_pf is not None and (acct_pf.cash_balance or acct_pf.positions.exists()):
            from apps.portfolios.sleeves import book_value

            eq = book_value(acct_pf)
            if eq is not None and record_snapshot(acct_pf, equity=eq, cash=acct_pf.cash_balance):
                snapshots += 1
        if fund.state != AutonomousFund.STATE_ACTIVE:
            continue
        try:
            res = evaluate_fund_drawdown(fund)
            if res.get("halted"):
                fund_halts += 1
                from apps.notifications.autopilot import notify_fund

                notify_fund(
                    fund,
                    "aggregate drawdown breached — all accounts halted. "
                    "Clearing the halt on the Fund page re-arms the breakers "
                    "from current equity.",
                )
        except Exception:  # noqa: BLE001
            log.exception("fund drawdown eval failed fund=%s", fund.pk)

    released = release_pending_open_orders()
    return {
        "halted": halted,
        "fund_halts": fund_halts,
        "snapshots": snapshots,
        "released": released.get("released", 0),
    }


@shared_task(name="apps.portfolios.tasks_autopilot.release_pending_open_orders")
def release_pending_open_orders() -> dict:
    """Submit locally-held pending_open orders once the market opens (§6.6).
    Daily caps are evaluated HERE (at release), not at create time, so a
    Friday-close batch held over the weekend submits Monday. Released orders are
    venue-fitted to the account's net position at submit (``autopilot.venue_fit``)."""
    if skip_when_offline("release_pending_open_orders"):
        return {"status": "skipped_offline"}
    from apps.brokers.market_calendar import is_market_open
    from apps.brokers.models import BrokerOrder
    from apps.portfolios.autopilot import submit_held_order

    if not is_market_open():
        return {"released": 0, "reason": "market closed"}
    now = timezone.now()
    held = list(
        BrokerOrder.objects.filter(
            status=BrokerOrder.STATUS_PENDING_OPEN, release_after__lte=now,
        ).select_related("broker_account").order_by("created_at", "id")
    )
    # P14: N sleeves share one account, so two held orders can name the same
    # ticker on opposite sides (sleeve A buys XLE while sleeve B trims it).
    # Alpaca rejects the second as a potential wash trade while the first is
    # live — so release one order per (account, ticker) per tick and DEFER the
    # rest to the next minute, after the earlier one has filled. Orders already
    # live at the broker for the ticker defer too.
    live = {
        (o.broker_account_id, o.ticker.upper())
        for o in BrokerOrder.objects.filter(
            broker_account_id__in={o.broker_account_id for o in held},
            status__in=BrokerOrder.OPEN_STATUSES,
        ).only("broker_account_id", "ticker")
    }
    # Shadow-mode daily caps: compute what the caps WOULD have skipped for this
    # batch, per account, and record it on the run audit. Never blocks (owner
    # decision) — the emission-time caps stay the only enforcement point.
    from apps.portfolios.release_caps import evaluate_release_caps

    shadow: dict[int, dict] = {}
    by_account: dict[int, list] = {}
    for order in held:
        by_account.setdefault(order.broker_account_id, []).append(order)
    for orders in by_account.values():
        account = orders[0].broker_account
        try:
            shadow[account.id] = evaluate_release_caps(account, orders)
        except Exception:  # noqa: BLE001 — shadow accounting never blocks a release
            log.exception("release cap shadow failed account=%s", account.pk)

    released = 0
    deferred = 0
    outcomes: dict[int, dict[str, list[int]]] = {}
    for order in held:
        run_ids = list(order.autopilot_runs.values_list("id", flat=True))
        key = (order.broker_account_id, order.ticker.upper())
        if key in live:
            deferred += 1
            _tally(outcomes, run_ids, "skipped", order)
            continue
        try:
            if submit_held_order(order):
                released += 1
                live.add(key)
                _tally(outcomes, run_ids, "released", order)
            else:
                _tally(outcomes, run_ids, "failed", order)
        except Exception:  # noqa: BLE001 — one bad release can't block the rest
            log.exception("release failed order=%s", order.pk)
            _tally(outcomes, run_ids, "failed", order)

    from apps.portfolios.autopilot_audit import record_release_outcome

    for run_id, buckets in outcomes.items():
        record_release_outcome(
            run_id,
            released=buckets["released"], skipped=buckets["skipped"],
            failed=buckets["failed"],
            caps_shadow=shadow.get(buckets["account_id"]),
        )
    # NB: the shadow cap evaluation is deliberately NOT in this return value —
    # it lives on the run audit (and the fund member card), so the beat task's
    # published shape stays exactly as it was.
    return {"released": released, "candidates": len(held), "deferred": deferred}


def _tally(outcomes: dict, run_ids: list[int], bucket: str, order) -> None:
    """Bucket one released/deferred/rejected order under every run that owns it."""
    for run_id in run_ids:
        buckets = outcomes.setdefault(
            run_id, {"released": [], "skipped": [], "failed": [], "account_id": None},
        )
        buckets["account_id"] = order.broker_account_id
        buckets[bucket].append(order.pk)


def trigger_autopilot_now(autopilot: StrategyAutopilot) -> AutopilotRun:
    """Fire one cycle immediately (the `run-now` path). Creates an AutopilotRun
    keyed on now() and dispatches it — still fully autonomous downstream."""
    run = AutopilotRun.objects.create(
        autopilot=autopilot, fire_time_utc=timezone.now(),
        status=AutopilotRun.PENDING,
    )
    run_autopilot_cycle.delay(run.id)
    return run
