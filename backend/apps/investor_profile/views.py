"""Investor-profile API views (P3-prereq-5 WS-A + WS-F)."""
from __future__ import annotations

import datetime as dt
import logging

from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .analysis import DEFAULT_MODEL, is_known_model, safe_model_id
from .models import (
    InvestorProfileState,
    QuestionnaireResponse,
    compute_nudge,
)
from .questionnaire import (
    SCHEMA,
    SCHEMA_VERSION,
    QuestionnaireValidationError,
    validate_answers,
)
from .serializers import (
    InvestorProfileStateSerializer,
    QuestionnaireHistoryItemSerializer,
    QuestionnaireResponseSerializer,
)
from .tasks import run_profile_analysis
from .tune import (
    HORIZON_BANDS,
    PATIENCE_BANDS,
    RISK_BANDS,
    rederive_for_tune,
)

log = logging.getLogger(__name__)

# Plan §"Abuse / cost guard": 10 analyses per rolling 24h per user.
ANALYSIS_LIMIT_PER_24H = 10
TUNE_LIMIT_PER_24H = 30


def _err(message: str, code: int = status.HTTP_400_BAD_REQUEST) -> Response:
    return Response({"detail": message}, status=code)


def _get_or_create_state(user) -> InvestorProfileState:
    state, _ = InvestorProfileState.objects.get_or_create(user=user)
    return state


def _build_profile_bundle(user) -> dict:
    state = _get_or_create_state(user)
    active = QuestionnaireResponse.objects.active_for(user)
    latest = (
        QuestionnaireResponse.objects.filter(user=user)
        .order_by("-created_at")
        .first()
    )
    nudge = compute_nudge(state, has_done_questionnaire=active is not None)

    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "joined_at": user.date_joined.isoformat() if getattr(user, "date_joined", None) else None,
        },
        "has_questionnaire": active is not None,
        "active": (
            QuestionnaireResponseSerializer(active).data if active is not None else None
        ),
        "latest": (
            {
                "id": latest.id,
                "status": latest.analysis_status,
                "source": latest.source,
                "created_at": latest.created_at.isoformat(),
                "error_message": latest.error_message,
            }
            if latest is not None
            else None
        ),
        "state": InvestorProfileStateSerializer(state).data,
        "nudge": nudge,
        "schema_version": SCHEMA_VERSION,
    }


class ProfileBundleView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response(_build_profile_bundle(request.user))


class QuestionnaireSchemaView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response(SCHEMA)


class QuestionnaireSubmitView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request) -> Response:
        body = request.data or {}
        answers = body.get("answers")
        model_id = safe_model_id(body.get("model_id") or DEFAULT_MODEL)
        try:
            normalized = validate_answers(answers, schema_version=SCHEMA_VERSION)
        except QuestionnaireValidationError as exc:
            return _err(str(exc))

        if model_id != DEFAULT_MODEL and not is_known_model(model_id):
            return _err(
                f"Unknown model id: {model_id}. Pick one from the model catalog.",
            )

        # Cost guard — count completed/in-flight rows in last 24h.
        cutoff = timezone.now() - dt.timedelta(hours=24)
        recent = QuestionnaireResponse.objects.filter(
            user=request.user,
            source=QuestionnaireResponse.SOURCE_QUESTIONNAIRE,
            created_at__gte=cutoff,
        ).count()
        if recent >= ANALYSIS_LIMIT_PER_24H:
            return _err(
                f"You can submit up to {ANALYSIS_LIMIT_PER_24H} questionnaires "
                "per 24 hours. Try again later.",
                code=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        response = QuestionnaireResponse.objects.create(
            user=request.user,
            source=QuestionnaireResponse.SOURCE_QUESTIONNAIRE,
            schema_version=SCHEMA_VERSION,
            answers=normalized,
            model_id=model_id,
            analysis_status=QuestionnaireResponse.PENDING,
        )
        _get_or_create_state(request.user)
        try:
            run_profile_analysis.delay(response.id)
        except Exception:  # noqa: BLE001 — broker down: run inline
            log.exception("celery enqueue failed; running profile analysis inline")
            try:
                run_profile_analysis(response.id)
            except Exception:  # noqa: BLE001
                log.exception("inline profile analysis also failed")
        response.refresh_from_db()
        return Response(
            QuestionnaireResponseSerializer(response).data,
            status=status.HTTP_201_CREATED,
        )


class QuestionnaireDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, pk: int) -> Response:
        try:
            response = QuestionnaireResponse.objects.get(pk=pk, user=request.user)
        except QuestionnaireResponse.DoesNotExist:
            return _err("Not found", code=status.HTTP_404_NOT_FOUND)
        return Response(QuestionnaireResponseSerializer(response).data)


class QuestionnaireHistoryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        rows = QuestionnaireResponse.objects.filter(user=request.user).order_by(
            "-created_at"
        )[:50]
        return Response(
            {"items": QuestionnaireHistoryItemSerializer(rows, many=True).data}
        )


class QuestionnaireTuneView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request, pk: int) -> Response:
        try:
            parent = QuestionnaireResponse.objects.get(pk=pk, user=request.user)
        except QuestionnaireResponse.DoesNotExist:
            return _err("Not found", code=status.HTTP_404_NOT_FOUND)
        if parent.analysis_status != QuestionnaireResponse.DONE:
            return _err("Can only tune a completed profile.")

        cutoff = timezone.now() - dt.timedelta(hours=24)
        recent_tunes = QuestionnaireResponse.objects.filter(
            user=request.user,
            source=QuestionnaireResponse.SOURCE_TUNED,
            created_at__gte=cutoff,
        ).count()
        if recent_tunes >= TUNE_LIMIT_PER_24H:
            return _err(
                f"You can fine-tune up to {TUNE_LIMIT_PER_24H} times per 24 hours.",
                code=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        body = request.data or {}
        risk_band = body.get("risk_band")
        horizon_band = body.get("horizon_band")
        patience_band = body.get("patience_band")
        if risk_band is None and horizon_band is None and patience_band is None:
            return _err("Provide at least one of risk_band/horizon_band/patience_band.")
        if risk_band is not None and risk_band not in RISK_BANDS:
            return _err(f"Unknown risk_band: {risk_band}")
        if horizon_band is not None and horizon_band not in HORIZON_BANDS:
            return _err(f"Unknown horizon_band: {horizon_band}")
        if patience_band is not None and patience_band not in PATIENCE_BANDS:
            return _err(f"Unknown patience_band: {patience_band}")

        try:
            new_analysis = rederive_for_tune(
                parent_analysis=parent.analysis or {},
                parent_answers=parent.answers or {},
                risk_band=risk_band,
                horizon_band=horizon_band,
                patience_band=patience_band,
            )
        except ValueError as exc:
            return _err(str(exc))

        tuned = QuestionnaireResponse.objects.create(
            user=request.user,
            source=QuestionnaireResponse.SOURCE_TUNED,
            derived_from=parent,
            schema_version=parent.schema_version,
            answers=parent.answers,
            model_id="(manual tune)",
            analysis_status=QuestionnaireResponse.DONE,
            analyzed_at=timezone.now(),
            analysis=new_analysis.model_dump(),
            profile_summary=new_analysis.summary,
            agent_brief=new_analysis.agent_brief,
        )
        return Response(
            QuestionnaireResponseSerializer(tuned).data,
            status=status.HTTP_201_CREATED,
        )


class ProfileStateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request: Request) -> Response:
        state = _get_or_create_state(request.user)
        body = request.data or {}
        if "apply_to_runs" in body:
            val = bool(body["apply_to_runs"])
            state.apply_to_runs = val
            state.save(update_fields=["apply_to_runs", "updated_at"])
        return Response(InvestorProfileStateSerializer(state).data)


class ProfileNudgeDismissView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request) -> Response:
        state = _get_or_create_state(request.user)
        state.nudge_dismiss_count = (state.nudge_dismiss_count or 0) + 1
        state.nudge_last_dismissed_at = timezone.now()
        state.save(
            update_fields=[
                "nudge_dismiss_count",
                "nudge_last_dismissed_at",
                "updated_at",
            ]
        )
        return Response(
            {
                "nudge_dismiss_count": state.nudge_dismiss_count,
                "nudge_last_dismissed_at": (
                    state.nudge_last_dismissed_at.isoformat()
                    if state.nudge_last_dismissed_at
                    else None
                ),
            }
        )
