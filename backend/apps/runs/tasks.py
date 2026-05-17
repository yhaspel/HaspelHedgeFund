"""Celery task that drives one Run through the council LangGraph."""
from __future__ import annotations

import logging
from decimal import Decimal

from celery import shared_task
from django.db.models import Sum
from django.utils import timezone

from hedgefund_agents.analytical.valuation import run_valuation  # noqa: F401 — register spec
from hedgefund_agents.graphs.council import (
    ANALYTICAL_NODES,
    build_council_graph,
)
from hedgefund_agents.models import LLMCall
from hedgefund_agents.personas import ALL_PERSONAS
from hedgefund_agents.registry import get_data_provider, get_filings_provider
from hedgefund_agents.versioning import ensure_versions_synced, snapshot_versions

from .models import AgentMessage, Decision, Run

log = logging.getLogger(__name__)

ANALYTICAL_AGENTS = list(ANALYTICAL_NODES.keys())
PIPELINE_AGENTS = ["risk_manager", "portfolio_manager"]


@shared_task
def execute_run(run_id: int) -> None:
    run = Run.objects.get(pk=run_id)
    run.status = Run.RUNNING
    run.save(update_fields=["status"])

    selected_personas = list(run.personas or ALL_PERSONAS)
    graph = build_council_graph(personas=selected_personas)
    data_provider = get_data_provider()
    filings_provider = get_filings_provider()

    ensure_versions_synced()
    run.agent_versions = snapshot_versions(
        ANALYTICAL_AGENTS + selected_personas + PIPELINE_AGENTS
    )
    run.save(update_fields=["agent_versions"])

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
            _persist_outputs(run, final_state, selected_personas)

        run.status = Run.DONE
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at"])
    except Exception as exc:  # pragma: no cover
        log.exception("Run %s failed", run_id)
        run.status = Run.FAILED
        run.error_message = f"{type(exc).__name__}: {exc}"[:2000]
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error_message", "finished_at"])
        raise
    finally:
        total = LLMCall.objects.filter(run=run).aggregate(s=Sum("cost_usd"))["s"] or Decimal("0")
        Run.objects.filter(pk=run.pk).update(total_cost_usd=total)


def _persist_outputs(run: Run, state: dict, selected_personas: list[str]) -> None:
    agent_keys = ANALYTICAL_AGENTS + selected_personas + ["risk"]
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
