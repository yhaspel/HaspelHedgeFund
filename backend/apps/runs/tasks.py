"""Celery task that drives one Run through the LangGraph pipeline."""
from __future__ import annotations

import logging
from decimal import Decimal

from celery import shared_task
from django.utils import timezone

from hedgefund_agents.graphs.single_persona import build_graph
from hedgefund_agents.models import LLMCall
from hedgefund_agents.registry import (
    get_data_provider,
    get_filings_provider,
)

from .models import AgentMessage, Decision, Run

log = logging.getLogger(__name__)


@shared_task
def execute_run(run_id: int) -> None:
    run = Run.objects.get(pk=run_id)
    run.status = Run.RUNNING
    run.save(update_fields=["status"])

    graph = build_graph()
    data_provider = get_data_provider()
    filings_provider = get_filings_provider()

    try:
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
            _persist_outputs(run, final_state)

        total = LLMCall.objects.filter(run=run).aggregate(
            from_=__import__("django.db.models", fromlist=["Sum"]).Sum("cost_usd")
        )["from_"] or Decimal("0")
        run.total_cost_usd = total
        run.status = Run.DONE
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at", "total_cost_usd"])
    except Exception as exc:  # pragma: no cover (defensive)
        log.exception("Run %s failed", run_id)
        run.status = Run.FAILED
        run.error_message = f"{type(exc).__name__}: {exc}"[:2000]
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error_message", "finished_at"])
        raise


def _persist_outputs(run: Run, state: dict) -> None:
    for agent_name in ("fundamentals", "technicals", "buffett"):
        payload = state.get(agent_name)
        if payload is None:
            continue
        AgentMessage.objects.create(
            run=run, agent_name=agent_name, parsed_output=payload
        )
    decision = state.get("decision")
    if decision:
        Decision.objects.create(
            run=run,
            ticker=decision["ticker"],
            action=decision["action"],
            confidence=decision["confidence"],
            rationale=decision["rationale"],
            dissenting_views=decision.get("dissenting_views", []),
        )
