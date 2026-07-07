"""Celery task that drives one Run through the council LangGraph."""
from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from apps.backtests.exceptions import BudgetExceeded
from apps.data.providers.factory import (
    get_edgar_provider,
    get_fmp_provider,
    get_ownership_provider,
)
from apps.graphs.compiler import resolve_graph
from hedgefund_agents.analytical.valuation import run_valuation  # noqa: F401 — register spec
from hedgefund_agents.graphs.council import ANALYTICAL_NODES
from hedgefund_agents.models import LLMCall
from hedgefund_agents.personas import ALL_PERSONAS
from hedgefund_agents.versioning import ensure_versions_synced, snapshot_versions

from .evidence import build_evidence, build_risk_context
from .models import AgentMessage, Decision, Run

NOT_APPLIED_EMPTY_META = {
    "applied": False,
    "response_id": None,
    "schema_version": None,
    "agent_brief": "",
    "reason": "",
}


def _resolve_investor_profile(run: Run) -> tuple[dict, dict]:
    """Resolve the investor profile for a run, once per ``execute_run``.

    Returns ``(profile_ctx, profile_meta)`` where ``profile_ctx == {}`` means
    no personalization (every consumer is a no-op). The metadata is written to
    ``run.investor_profile_applied`` for the audit / Personalized badge.

    Eligibility (plan §1, §3, §11):
        - ad-hoc runs always eligible;
        - strategy-sourced runs eligible only when the strategy has
          ``apply_investor_profile`` on;
        - backtests are a SEPARATE path (apps.backtests) that never calls
          this function — the key is unset there.
    """
    def not_applied(reason: str) -> tuple[dict, dict]:
        meta = dict(NOT_APPLIED_EMPTY_META)
        meta["reason"] = reason
        return {}, meta

    if run.source == Run.ADHOC:
        pass
    elif run.source == Run.STRATEGY:
        portfolio_target = getattr(run, "portfolio_target", None)
        strategy = (
            getattr(portfolio_target, "strategy", None)
            if portfolio_target is not None
            else None
        )
        if not (strategy and getattr(strategy, "apply_investor_profile", False)):
            return not_applied("strategy_opt_out")
    else:  # unknown sources are excluded by default
        return not_applied("strategy_opt_out")

    try:
        from apps.investor_profile.models import (
            InvestorProfileState,
            QuestionnaireResponse,
        )
    except ImportError:  # pragma: no cover — app always installed in prod
        return not_applied("no_profile")

    state = InvestorProfileState.objects.filter(user=run.user).first()
    if state is not None and not state.apply_to_runs:
        return not_applied("personalization_off")

    active = QuestionnaireResponse.objects.active_for(run.user)
    if active is None:
        return not_applied("no_profile")

    analysis = active.analysis or {}
    ctx = {
        "response_id": active.id,
        "investor_type": analysis.get("investor_type", ""),
        "risk_band": analysis.get("risk_band", ""),
        "horizon_band": analysis.get("horizon_band", ""),
        "agent_brief": active.agent_brief or "",
    }
    meta = {
        "applied": True,
        "response_id": active.id,
        "schema_version": active.schema_version,
        "agent_brief": active.agent_brief or "",
        "reason": "",
    }
    return ctx, meta

def _resolve_persona_evolution(run: Run) -> tuple[dict[str, str], dict]:
    """Resolve persona-evolution revisions for a run, once per ``execute_run``.

    Live-only (decision 7). The PIT resolver picks the latest revision whose
    ``as_of_date <= run.as_of_date`` per evolvable persona (§6.4) — so an
    ad-hoc past-dated run never sees an evolving note dated after its
    as-of date.

    Returns ``(ctx, meta)`` where ``ctx == {persona_name: composite_markdown}``
    is what gets threaded into ``state["persona_evolution"]``, and ``meta``
    is the audit blob persisted to ``run.persona_evolution_applied``.
    """
    try:
        from apps.persona_evolution.engine import (
            resolve_evolution_for_run,
            revision_seq_map_for_run,
        )
    except ImportError:  # pragma: no cover — app always installed in prod
        return {}, {}

    try:
        ctx = resolve_evolution_for_run(run.as_of_date)
        seq_map = revision_seq_map_for_run(run.as_of_date)
    except Exception:  # noqa: BLE001 — never let evolution wiring break a run
        log.exception("persona_evolution: resolver failed for run=%s", run.pk)
        return {}, {}

    meta = {
        "applied": bool(ctx),
        "as_of_date": run.as_of_date.isoformat(),
        "revisions": seq_map,
    }
    return ctx, meta


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
    swept_ids: list[int] = []
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
        swept_ids.append(run.id)
    if swept:
        log.warning("orphan sweep marked %d runs failed", swept)
        # P5-SH WS2.2: a sweep > 0 means a worker died mid-run — tell the operator.
        try:
            from apps.notifications.operator import notify_orphan_sweep

            notify_orphan_sweep(swept, run_ids=swept_ids)
        except Exception:  # pragma: no cover — never let an alert break the sweep
            log.exception("orphan-sweep operator alert failed")
    return {"swept": swept}


# L3: a per-task wall-clock cap so a run is GUARANTEED to terminate even if every
# inner timeout/fallback somehow fails. soft_time_limit raises
# SoftTimeLimitExceeded inside the task (caught below → clean FAILED); the hard
# time_limit SIGKILLs the worker child as a last-resort backstop. Set per-task
# (not globally) so legitimately-long jobs (13F bulk ingest) aren't collateral.
@shared_task(
    soft_time_limit=getattr(settings, "RUN_SOFT_TIME_LIMIT_SECONDS", 600),
    time_limit=getattr(settings, "RUN_HARD_TIME_LIMIT_SECONDS", 720),
)
def execute_run(run_id: int) -> None:
    run = Run.objects.get(pk=run_id)
    run.status = Run.RUNNING
    run.save(update_fields=["status"])

    selected_personas = list(run.personas or ALL_PERSONAS)
    # P4c: resolve from the run's AgentGraphVersion when ENABLE_DB_GRAPHS is on;
    # otherwise (or on compile failure) fall back to the hardcoded council with
    # run.personas. The submission serializer already flattened the chosen
    # version's models into run.model_overrides and its personas into
    # run.personas, so selected_personas stays correct for both paths.
    graph = resolve_graph(run)
    data_provider = get_fmp_provider(user=run.user)
    filings_provider = get_edgar_provider()
    ownership_provider = get_ownership_provider(user=run.user)

    ensure_versions_synced()
    run.agent_versions = snapshot_versions(
        ANALYTICAL_AGENTS + selected_personas + PIPELINE_AGENTS
    )
    run.save(update_fields=["agent_versions"])

    # P5-SH WS2.1: bind run_id for the run's execution so every worker log line is
    # greppable. Bound immediately before the try (and reset in its finally) so a
    # failure during the setup above can never leak it into the next task on this
    # worker process. The Celery task_postrun signal is a second-line backstop.
    from hedgefund.logging_filters import run_id_var

    _run_ctx_token = run_id_var.set(str(run_id))
    try:
        # P4-OFF WS-1.5: force the all-local preset at the single execution seam.
        # Covers ad-hoc, scheduled watchlist runs (they call execute_run), and
        # reruns of pre-offline runs whose stored model_overrides may carry cloud
        # slugs. Inside the try so a "no local model" hard-error marks the run
        # FAILED with the actionable message instead of leaving it stuck RUNNING.
        model_overrides = run.model_overrides or {}
        if getattr(settings, "OFFLINE_MODE", False):
            from apps.models_catalog.offline import offline_model_overrides

            model_overrides = offline_model_overrides(run.user)
            log.info("offline_run run_id=%s forced_preset=local", run.id)

        # P02a review: label the run's risk context (stub vs real portfolio).
        # Strategy-sourced runs already see a real portfolio downstream; ad-hoc
        # single-ticker runs use the $100K stub in the risk manager.
        run.risk_context = build_risk_context(portfolio=None)
        run.save(update_fields=["risk_context"])

        # P3-prereq-5 WS-C/WS-G: resolve the investor profile once per run.
        profile_ctx, profile_meta = _resolve_investor_profile(run)
        run.investor_profile_applied = profile_meta
        run.save(update_fields=["investor_profile_applied"])

        # P3-D WS-D: resolve persona-evolution revisions once per run.
        # Live-only: the backtest engine never imports apps.persona_evolution,
        # so this code path cannot reach a backtest.
        persona_evolution_ctx, persona_evolution_meta = _resolve_persona_evolution(run)
        run.persona_evolution_applied = persona_evolution_meta
        run.save(update_fields=["persona_evolution_applied"])

        for ticker in run.tickers:
            initial_state = {
                "ticker": ticker,
                "as_of_date": run.as_of_date,
                "run_id": run.id,
                "user_id": run.user_id,
                "model_overrides": model_overrides,
                "data_provider": data_provider,
                "filings_provider": filings_provider,
                "ownership_provider": ownership_provider,
                "investor_profile": profile_ctx,
                "persona_evolution": persona_evolution_ctx,
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
        # P3b: denormalized FTS text from the now-persisted decisions/messages.
        try:
            from .search import build_search_text

            run.search_text = _scrub_nul(build_search_text(run))
        except Exception:  # never let search indexing fail the run
            log.exception("search_text build failed for run=%s", run_id)
        run.status = Run.DONE
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at", "evidence", "search_text"])
    except Exception as exc:  # pragma: no cover
        log.exception("Run %s failed", run_id)
        # If the user already cancelled this run via the API, don't clobber
        # the cancelled state with FAILED — the SIGTERM that revoke()
        # delivered will surface here as an unhandled exception.
        current = Run.objects.filter(pk=run_id).values_list("status", flat=True).first()
        if current != Run.CANCELLED:
            # L3: the wall-clock cap fired — give the operator a clear, actionable
            # message instead of a bare "SoftTimeLimitExceeded".
            if isinstance(exc, SoftTimeLimitExceeded):
                soft = getattr(settings, "RUN_SOFT_TIME_LIMIT_SECONDS", 600)
                msg = (
                    f"Run exceeded the {soft}s wall-clock limit and was stopped. "
                    "An LLM route was likely stalling; the run did not hang the "
                    "worker (see self-healing layers L1/L2)."
                )
            elif isinstance(exc, BudgetExceeded):
                # P5-SH WS1.2: the mid-run LLM-spend cap tripped. Prefix the
                # message with the machine-readable "budget_exceeded:" token the
                # API/UI and operator alert key off.
                msg = f"budget_exceeded: {exc}"
            else:
                msg = f"{type(exc).__name__}: {exc}"
            run.status = Run.FAILED
            run.error_message = msg[:2000]
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "error_message", "finished_at"])
            # P5-SH WS2.2: alert the operator that a run failed (covers the
            # budget-cap abort too). Best-effort — never let notification errors
            # mask the original failure.
            try:
                from apps.notifications.operator import notify_run_failed

                notify_run_failed(run)
            except Exception:  # pragma: no cover — defensive
                log.exception("operator run-failed alert failed for run=%s", run_id)
        raise
    finally:
        # Exclude unknown-price sentinel rows (cost_usd < 0) from the total.
        total = (
            LLMCall.objects.filter(run=run, cost_usd__gt=0)
            .aggregate(s=Sum("cost_usd"))["s"]
            or Decimal("0")
        )
        Run.objects.filter(pk=run.pk).update(total_cost_usd=total)
        run_id_var.reset(_run_ctx_token)


def _scrub_nul(obj):
    """Recursively strip NUL bytes (U+0000) from strings in an LLM-output blob.

    PostgreSQL text/jsonb columns cannot store \\u0000 and raise
    `DataError: unsupported Unicode escape sequence` on insert. Models
    occasionally emit a stray NUL (observed: a news_digest payload), which used
    to crash the AgentMessage insert and fail the whole run AFTER every LLM call
    had already succeeded. Scrubbing at the persistence boundary keeps the
    in-memory state untouched for downstream aggregation."""
    if isinstance(obj, str):
        return obj.replace("\x00", "") if "\x00" in obj else obj
    if isinstance(obj, dict):
        return {k: _scrub_nul(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_nul(v) for v in obj]
    return obj


def _persist_outputs(run: Run, state: dict, selected_personas: list[str]) -> None:
    agent_keys = ANALYTICAL_AGENTS + selected_personas + ["risk", "pm_decision", "cio"]
    degraded: list[str] = []
    for agent_name in agent_keys:
        payload = state.get(agent_name)
        if payload is None:
            continue
        # Self-heal visibility: a node that the live-tolerance layer degraded to
        # a null signal carries a private "_degraded" marker. Strip it and record
        # it on the AgentMessage.status so the operator can see the council ran
        # with this agent degraded instead of a silent all-"ok" transcript.
        msg_status = "ok"
        if isinstance(payload, dict) and payload.pop("_degraded", False):
            msg_status = "degraded"
            degraded.append(agent_name)
        AgentMessage.objects.create(
            run=run, agent_name=agent_name,
            parsed_output=_scrub_nul(payload), status=msg_status,
        )
    if degraded:
        log.warning(
            "run %s completed with %d degraded agent(s) (self-heal): %s",
            run.pk, len(degraded), ", ".join(degraded),
        )

    decision = state.get("decision")
    if isinstance(decision, dict):
        decision.pop("_degraded", None)
        decision = _scrub_nul(decision)
    risk_overrides = state.get("risk", {})
    if isinstance(risk_overrides, dict):
        risk_overrides = _scrub_nul(
            {k: v for k, v in risk_overrides.items() if k != "_degraded"}
        )
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
            risk_overrides=risk_overrides,
        )
