"""Persona-evolution API views (P3-D WS-F)."""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from rest_framework import permissions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .engine import month_to_date_cost_usd
from .models import (
    PersonaEvolutionProfile,
    PersonaEvolutionSettings,
)
from .serializers import (
    PersonaEvolutionProfileSerializer,
    PersonaEvolutionRevisionSerializer,
    PersonaEvolutionSettingsSerializer,
)
from .tasks import evolve_personas

log = logging.getLogger(__name__)


def _get_or_create_settings(user) -> PersonaEvolutionSettings:
    obj, _ = PersonaEvolutionSettings.objects.get_or_create(user=user)
    return obj


def _err(message: str, code: int = status.HTTP_400_BAD_REQUEST) -> Response:
    return Response({"detail": message}, status=code)


class PersonaEvolutionSettingsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        settings_row = _get_or_create_settings(request.user)
        data = PersonaEvolutionSettingsSerializer(settings_row).data
        data["month_to_date_cost_usd"] = str(month_to_date_cost_usd())
        return Response(data)

    def patch(self, request: Request) -> Response:
        settings_row = _get_or_create_settings(request.user)
        body = request.data or {}
        changed: list[str] = []
        if "enabled" in body:
            settings_row.enabled = bool(body["enabled"])
            changed.append("enabled")
        if "cadence" in body:
            cadence = str(body["cadence"])
            valid = {c for c, _ in PersonaEvolutionSettings.CADENCE_CHOICES}
            if cadence not in valid:
                return _err(f"Invalid cadence: {cadence}")
            settings_row.cadence = cadence
            changed.append("cadence")
        if "model_id" in body:
            settings_row.model_id = str(body["model_id"] or "")
            changed.append("model_id")
        if "web_search_enabled" in body:
            settings_row.web_search_enabled = bool(body["web_search_enabled"])
            changed.append("web_search_enabled")
        if "monthly_cost_cap_usd" in body:
            try:
                cap = Decimal(str(body["monthly_cost_cap_usd"]))
            except (InvalidOperation, TypeError):
                return _err("monthly_cost_cap_usd must be a number.")
            if cap < 0:
                return _err("monthly_cost_cap_usd must be >= 0.")
            settings_row.monthly_cost_cap_usd = cap
            changed.append("monthly_cost_cap_usd")
        if changed:
            settings_row.save(update_fields=changed + ["updated_at"])
        data = PersonaEvolutionSettingsSerializer(settings_row).data
        data["month_to_date_cost_usd"] = str(month_to_date_cost_usd())
        return Response(data)


class PersonaEvolutionProfileListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        rows = PersonaEvolutionProfile.objects.all().select_related("current_revision")
        return Response(
            {"items": PersonaEvolutionProfileSerializer(rows, many=True).data}
        )


class PersonaEvolutionRevisionsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, persona_name: str) -> Response:
        profile = PersonaEvolutionProfile.objects.filter(
            persona_name=persona_name
        ).first()
        if profile is None:
            return _err("Not found", code=status.HTTP_404_NOT_FOUND)
        revs = profile.revisions.all().order_by("-seq")[:50]
        return Response(
            {
                "persona": PersonaEvolutionProfileSerializer(profile).data,
                "items": PersonaEvolutionRevisionSerializer(revs, many=True).data,
            }
        )


class PersonaEvolutionRunNowView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request) -> Response:
        """Enqueue an evolution run on the Celery worker and return 202.

        The cycle is long-running (one ~10-30s LLM round per persona). We
        dispatch via ``.delay()`` so the HTTP request returns immediately
        and the UI can poll ``GET /profiles/`` for per-persona
        ``current_cycle_started_at`` updates. State lives in the DB, so a
        page reload during the run still shows the in-flight persona.
        """
        body = request.data or {}
        persona = body.get("persona") or None
        # Make sure the row exists so the task can find an "enabled" user
        # downstream — but for the on-demand trigger we don't require enabled.
        _get_or_create_settings(request.user)
        try:
            evolve_personas.delay(
                user_id=request.user.id, persona=persona, force=True
            )
            return Response(
                {"queued": True, "persona": persona},
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception:  # noqa: BLE001 — broker unavailable: inline fallback
            log.exception(
                "persona_evolution: celery enqueue failed; running inline"
            )
            try:
                evolve_personas(
                    user_id=request.user.id, persona=persona, force=True
                )
                return Response(
                    {"queued": False, "persona": persona, "ran_inline": True},
                    status=status.HTTP_200_OK,
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("persona_evolution: inline fallback also failed")
                return _err(
                    f"Run failed: {exc.__class__.__name__}: {exc}",
                    code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
