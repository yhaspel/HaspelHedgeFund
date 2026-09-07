"""Leaderboard API (P3b).

Agent + model scorecards are global (single-tenant: they reflect the user's own
runs). Strategy scorecards are filtered to the requesting user's strategies; the
per-flavor view is honestly labeled "your N strategies of this flavor".
"""
from __future__ import annotations

from django.db.models import F, Max
from django.utils import timezone
from rest_framework import permissions
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .compute import (
    DECISION_DETAIL_LIMIT,
    WINDOWS_STRATEGY,
    _cutoff,
    agent_decision_detail,
    council_alpha_series,
)
from .models import AgentScorecard, ModelScorecard, StrategyScorecard
from .serializers import (
    AgentScorecardSerializer,
    ModelScorecardSerializer,
    StrategyScorecardSerializer,
)


def _latest_as_of(model, window: str, **extra):
    return model.objects.filter(window=window, **extra).aggregate(m=Max("as_of"))["m"]


def _provisional_last(qs, *order):
    """Provisional (small-sample) rows always sort BELOW real ones, whatever the
    caller asked to sort by — a Sharpe-less 3-cycle row must never head the
    table just because the column it was ranked on is null-friendly."""
    return qs.order_by("provisional", *order)


class AgentLeaderboardView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        window = request.query_params.get("window", "90d")
        as_of = _latest_as_of(AgentScorecard, window, user=request.user)
        rows = (
            _provisional_last(
                AgentScorecard.objects.filter(
                    window=window, as_of=as_of, user=request.user,
                ),
                F("hit_rate").desc(nulls_last=True), "brier_score",
            )
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
        qs = _provisional_last(qs, order)
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
        as_of = _latest_as_of(
            StrategyScorecard, window, strategy__isnull=True, user=request.user,
        )
        qs = (
            StrategyScorecard.objects.filter(
                window=window, as_of=as_of, strategy__isnull=True, user=request.user,
            ).order_by("flavor")
            if as_of else StrategyScorecard.objects.none()
        )
        return Response({
            "window": window,
            "as_of": as_of,
            "note": "Single-tenant: each row aggregates your own strategies of that flavor.",
            "rows": StrategyScorecardSerializer(qs, many=True).data,
        })


class StrategyCouncilAlphaView(APIView):
    """Drill-down: per-cycle realised vs council-free-baseline returns for one of
    the user's strategies — the series behind the council-alpha chart."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, strategy_id: int) -> Response:
        from apps.portfolios.models import PortfolioStrategy

        strategy = (
            PortfolioStrategy.objects.filter(pk=strategy_id, user=request.user).first()
        )
        if strategy is None:
            return Response({"detail": "Strategy not found."}, status=404)
        window = request.query_params.get("window", "90d")
        cutoff = _cutoff(WINDOWS_STRATEGY.get(window), timezone.localdate())
        return Response({
            "strategy_id": strategy.id,
            "strategy_name": strategy.name,
            "flavor": strategy.kind,
            "window": window,
            "rows": council_alpha_series(strategy, cutoff),
        })


class AgentDecisionsView(APIView):
    """Drill-down: the underlying decisions behind an agent's scorecard."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, agent_name: str) -> Response:
        window = request.query_params.get("window", "90d")

        def _int(name: str, default: int) -> int:
            try:
                return int(request.query_params.get(name, default))
            except (TypeError, ValueError):
                return default

        limit = max(1, min(_int("limit", DECISION_DETAIL_LIMIT), DECISION_DETAIL_LIMIT))
        offset = max(0, _int("offset", 0))
        decisions = agent_decision_detail(
            agent_name, window=window, user=request.user, limit=limit, offset=offset,
        )
        return Response({
            "agent_name": agent_name,
            "window": window,
            "limit": limit,
            "offset": offset,
            "decisions": decisions,
        })


class RecomputeView(APIView):
    """POST /api/leaderboard/recompute/ — recompute now (manual / testing)."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request) -> Response:
        from .compute import recompute_all

        result = recompute_all()
        as_of = result["as_of"]
        # The rebuild itself is global (it must be — every tenant's rows are
        # rewritten under the new maths), but the counts reported back are the
        # CALLER's own rows. ``models`` has no owner dimension and stays global.
        result["agents"] = AgentScorecard.objects.filter(
            as_of=as_of, user=request.user,
        ).count()
        result["strategies"] = StrategyScorecard.objects.filter(
            as_of=as_of, user=request.user,
        ).count()
        return Response(result)
