"""Leaderboard API (P3b).

Agent + model scorecards are global (single-tenant: they reflect the user's own
runs). Strategy scorecards are filtered to the requesting user's strategies; the
per-flavor view is honestly labeled "your N strategies of this flavor".
"""
from __future__ import annotations

from django.db.models import F, Max
from rest_framework import permissions
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .compute import agent_decision_detail
from .models import AgentScorecard, ModelScorecard, StrategyScorecard
from .serializers import (
    AgentScorecardSerializer,
    ModelScorecardSerializer,
    StrategyScorecardSerializer,
)


def _latest_as_of(model, window: str, **extra):
    return model.objects.filter(window=window, **extra).aggregate(m=Max("as_of"))["m"]


class AgentLeaderboardView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        window = request.query_params.get("window", "90d")
        as_of = _latest_as_of(AgentScorecard, window)
        rows = (
            AgentScorecard.objects.filter(window=window, as_of=as_of)
            .order_by(F("hit_rate").desc(nulls_last=True), "brier_score")
            if as_of else AgentScorecard.objects.none()
        )
        return Response({
            "window": window,
            "as_of": as_of,
            "rows": AgentScorecardSerializer(rows, many=True).data,
        })


class ModelLeaderboardView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        window = request.query_params.get("window", "90d")
        role = request.query_params.get("role")
        as_of = _latest_as_of(ModelScorecard, window)
        qs = ModelScorecard.objects.filter(window=window, as_of=as_of) if as_of \
            else ModelScorecard.objects.none()
        if role:
            qs = qs.filter(agent_role=role)
        qs = qs.order_by(F("cost_adjusted_return_bps").desc(nulls_last=True), "-n_decisions")
        return Response({
            "window": window,
            "as_of": as_of,
            "rows": ModelScorecardSerializer(qs, many=True).data,
        })


class StrategyLeaderboardView(APIView):
    """Per-strategy view — the requesting user's strategies side by side."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        window = request.query_params.get("window", "90d")
        as_of = _latest_as_of(StrategyScorecard, window, strategy__isnull=False)
        qs = (
            StrategyScorecard.objects.filter(
                window=window, as_of=as_of,
                strategy__isnull=False, strategy__user=request.user,
            ).select_related("strategy")
            if as_of else StrategyScorecard.objects.none()
        )
        sort = request.query_params.get("sort", "sharpe")
        order = F(sort).desc(nulls_last=True) if sort in {
            "sharpe", "sortino", "council_alpha_bps", "total_return_pct", "hit_rate"
        } else F("sharpe").desc(nulls_last=True)
        qs = qs.order_by(order)
        return Response({
            "window": window,
            "as_of": as_of,
            "rows": StrategyScorecardSerializer(qs, many=True).data,
        })


class FlavorBenchmarkView(APIView):
    """Per-flavor median aggregates across the user's strategies of each flavor."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        window = request.query_params.get("window", "90d")
        as_of = _latest_as_of(StrategyScorecard, window, strategy__isnull=True)
        qs = (
            StrategyScorecard.objects.filter(
                window=window, as_of=as_of, strategy__isnull=True
            ).order_by("flavor")
            if as_of else StrategyScorecard.objects.none()
        )
        return Response({
            "window": window,
            "as_of": as_of,
            "note": "Single-tenant: each row aggregates your own strategies of that flavor.",
            "rows": StrategyScorecardSerializer(qs, many=True).data,
        })


class AgentDecisionsView(APIView):
    """Drill-down: the underlying decisions behind an agent's scorecard."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, agent_name: str) -> Response:
        window = request.query_params.get("window", "90d")
        return Response({
            "agent_name": agent_name,
            "window": window,
            "decisions": agent_decision_detail(agent_name, window=window),
        })


class RecomputeView(APIView):
    """POST /api/leaderboard/recompute/ — recompute now (manual / testing)."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request) -> Response:
        from .compute import recompute_all

        return Response(recompute_all())
