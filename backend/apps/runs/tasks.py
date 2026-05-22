"""Celery task that drives one Run through the council LangGraph."""
from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal

from celery import shared_task
from django.db.models import Sum
from django.utils import timezone

from apps.data.providers.factory import get_edgar_provider, get_fmp_provider
from hedgefund_agents.analytical.valuation import run_valuation  # noqa: F401 — register spec
from hedgefund_agents.graphs.council import (
    ANALYTICAL_NODES,
    build_council_graph,
)
from hedgefund_agents.models import LLMCall
from hedgefund_agents.personas import ALL_PERSONAS
from hedgefund_agents.versioning import ensure_versions_synced, snapshot_versions

from .evidence import build_evidence, build_risk_context
from .models import AgentMessage, Decision, Run

log = logging.getLogger(__name__)

ANALYTICAL_AGENTS = list(ANALYTICAL_NODES.keys())
PIPELINE_AGENTS = [
    "risk_manager", "portfolio_manager", "cio", "macro", "news_digest",
]


ORPHAN_THRESHOLD_MIN = 15


@shared_task
def sweep_orphan_runs() -> dict:
    """Mark abandoned runs as failed.

    A run is "orphaned" if it's still in {queued, running} but has been sitting
    that way for > ORPHAN_THRESHOLD_MIN minutes AND the Celery broker has no
    active task with its task id. Happens after worker restarts /
    container recreation kills the subprocess before the task wrapper can
    record terminal status.
    """
    from hedgefund.celery import app as celery_app  # local: avoid load-time cycle

    cutoff = timezone.now() - dt.timedelta(minutes=ORPHAN_THRESHOLD_MIN)
    candidates = Run.objects.filter(
        status__in=Run.ACTIVE_STATUSES, created_at__lt=cutoff
    )
    if not candidates.exists():
        return {"swept": 0}

    active_ids: set[str] = set()
    try:
        inspect = celery_app.control.inspect(timeout=2.0)
        for _worker, tasks in (inspect.active() or {}).items():
            for t in tasks or []:
                tid = t.get("id")
                if tid:
                    active_ids.add(tid)
    except Exception:  # broker unreachable etc. — better to do nothing than to clobber live runs
        log.warning("orphan sweep: could not inspect active tasks; aborting")
        return {"swept": 0, "error": "inspect_failed"}

    swept = 0
    for run in candidates:
        if run.celery_task_id and run.celery_task_id in active_ids:
            continue  # still running, just slow
        run.status = Run.FAILED
        run.error_message = (
            "Orphaned: no active Celery task for this run; worker likely "
            "restarted mid-execution."
        )
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error_message", "finished_at"])
        swept += 1
    if swept:
        log.warning("orphan sweep marked %d runs failed", swept)
    return {"swept": swept}


@shared_task
def execute_run(run_id: int) -> None:
    run = Run.objects.get(pk=run_id)
    run.status = Run.RUNNING
    run.save(update_fields=["status"])

    selected_personas = list(run.personas or ALL_PERSONAS)
    graph = build_council_graph(personas=selected_personas)
    data_provider = get_fmp_provider(user=run.user)
    filings_provider = get_edgar_provider()

    ensure_versions_synced()
    run.agent_versions = snapshot_versions(
        ANALYTICAL_AGENTS + selected_personas + PIPELINE_AGENTS
    )
    run.save(update_fields=["agent_versions"])

    try:
        # P02a review: label the run's risk context (stub vs real portfolio).
        # Strategy-sourced runs already see a real portfolio downstream; ad-hoc
        # single-ticker runs use the $100K stub in the risk manager.
        run.risk_context = build_risk_context(portfolio=None)
        run.save(update_fields=["risk_context"])

        for ticker in run.tickers:
            initial_state = {
                "ticker": ticker,
                "as_of_date": run.as_of_date,
                "run_id": run.id,
                "model_overrides": run.model_overrides or {},
                "data_provider": data_provider,
                "filings_provider": filings_provider,
            }
            final_state = graph.invoke(initial_state)
            _persist_outputs(run, final_state, selected_personas)

        # P01 review: capture evidence/provenance after data has settled.
        try:
            primary_ticker = (run.tickers or [""])[0]
            if primary_ticker:
                run.evidence = build_evidence(primary_ticker, run.as_of_date)
        except Exception:  # never let evidence collection fail the run
            log.exception("evidence collection failed for run=%s", run_id)
        run.status = Run.DONE
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at", "evidence"])
    except Exception as exc:  # pragma: no cover
        log.exception("Run %s failed", run_id)
        # If the user already cancelled this run via the API, don't clobber
        # the cancelled state with FAILED — the SIGTERM that revoke()
        # delivered will surface here as an unhandled exception.
        current = Run.objects.filter(pk=run_id).values_list("status", flat=True).first()
        if current != Run.CANCELLED:
            run.status = Run.FAILED
            run.error_message = f"{type(exc).__name__}: {exc}"[:2000]
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "error_message", "finished_at"])
        raise
    finally:
        # Exclude unknown-price sentinel rows (cost_usd < 0) from the total.
        total = (
            LLMCall.objects.filter(run=run, cost_usd__gt=0)
            .aggregate(s=Sum("cost_usd"))["s"]
            or Decimal("0")
        )
        Run.objects.filter(pk=run.pk).update(total_cost_usd=total)


def _persist_outputs(run: Run, state: dict, selected_personas: list[str]) -> None:
    agent_keys = ANALYTICAL_AGENTS + selected_personas + ["risk", "pm_decision", "cio"]
    for agent_name in agent_keys:
        payload = state.get(agent_name)
        if payload is None:
            continue
        AgentMessage.objects.create(run=run, agent_name=agent_name, parsed_output=payload)

    decision = state.get("decision")
    if decision:
        Decision.objects.create(
            run=run,
            ticker=decision["ticker"],
            action=decision["action"],
            confidence=int(decision.get("aggregate_confidence", 0)),
            rationale=decision.get("rationale", ""),
            dissenting_views=decision.get("dissenting_personas", []),
            target_quantity=Decimal(str(decision.get("target_quantity", 0))),
            target_weight_pct=Decimal(str(decision.get("target_weight_pct", 0))),
            risk_overrides=state.get("risk", {}),
        )
