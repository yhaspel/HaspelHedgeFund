"""Celery tasks for the investor-profile app."""
from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from .analysis import analyze_questionnaire
from .models import QuestionnaireResponse

log = logging.getLogger(__name__)


@shared_task
def run_profile_analysis(response_id: int) -> dict:
    """Analyse a ``QuestionnaireResponse``; on success auto-seed the watchlist.

    Idempotent on ``response_id``: re-runs on a row already DONE will overwrite
    the existing analysis with a fresh one (cost recorded in LLMCall). A row
    already RUNNING (e.g. duplicate enqueue) short-circuits.
    """
    try:
        response = QuestionnaireResponse.objects.get(pk=response_id)
    except QuestionnaireResponse.DoesNotExist:
        log.warning("run_profile_analysis: response %s missing", response_id)
        return {"status": "missing"}

    if response.analysis_status == QuestionnaireResponse.RUNNING:
        log.info("run_profile_analysis: response %s already running", response_id)
        return {"status": "duplicate"}

    response.analysis_status = QuestionnaireResponse.RUNNING
    response.error_message = ""
    response.save(update_fields=["analysis_status", "error_message"])

    try:
        parsed = analyze_questionnaire(response)
    except Exception as exc:  # noqa: BLE001 — surface the error to the user
        log.exception("profile analysis failed for response %s", response_id)
        response.analysis_status = QuestionnaireResponse.FAILED
        response.error_message = f"{type(exc).__name__}: {exc}"[:1000]
        response.save(update_fields=["analysis_status", "error_message"])
        return {"status": "failed", "error": response.error_message}

    response.analysis = parsed.model_dump()
    response.profile_summary = parsed.summary or ""
    response.agent_brief = parsed.agent_brief or ""
    response.analyzed_at = timezone.now()
    response.analysis_status = QuestionnaireResponse.DONE
    response.save(
        update_fields=[
            "analysis",
            "profile_summary",
            "agent_brief",
            "analyzed_at",
            "analysis_status",
        ]
    )

    favorites = response.answers.get("favorite_tickers", []) if response.answers else []
    added = 0
    if favorites:
        try:
            from apps.watchlists.services import add_tickers_to_watchlist

            added = add_tickers_to_watchlist(response.user, favorites)
        except Exception:  # noqa: BLE001 — watchlist failure must not fail analysis
            log.exception(
                "watchlist auto-seed failed for response %s", response_id
            )
    return {"status": "done", "response_id": response_id, "watchlist_added": added}
