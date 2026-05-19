from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime

from rest_framework import generics, permissions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .borrow import StubBorrowProvider
from .models import (
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    Position,
    Universe,
    UniverseMembership,
)
from .serializers import (
    PortfolioSerializer,
    PortfolioTargetDetailSerializer,
    PortfolioTargetSummarySerializer,
    PositionSerializer,
    StrategySerializer,
    UniverseMembershipSerializer,
    UniverseSerializer,
)
from .tasks import daily_long_short_cycle, estimate_cycle


class UniverseListView(generics.ListAPIView):
    serializer_class = UniverseSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Universe.objects.filter(is_active=True).order_by("name")


class UniverseMembershipView(APIView):
    def get(self, request: Request, name: str) -> Response:
        try:
            u = Universe.objects.get(name=name)
        except Universe.DoesNotExist:
            return Response({"detail": "not found"}, status=404)
        members = UniverseMembership.objects.filter(universe=u).order_by("ticker")
        return Response({
            "universe": u.name,
            "members": UniverseMembershipSerializer(members, many=True).data,
        })


class PortfolioListCreateView(generics.ListCreateAPIView):
    serializer_class = PortfolioSerializer

    def get_queryset(self):
        return Portfolio.objects.filter(user=self.request.user).order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class PositionsView(generics.ListAPIView):
    serializer_class = PositionSerializer

    def get_queryset(self):
        return Position.objects.filter(
            portfolio__user=self.request.user, portfolio_id=self.kwargs["portfolio_id"]
        ).order_by("ticker")


class StrategyListCreateView(generics.ListCreateAPIView):
    serializer_class = StrategySerializer

    def get_queryset(self):
        return PortfolioStrategy.objects.filter(user=self.request.user).order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class StrategyDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = StrategySerializer

    def get_queryset(self):
        return PortfolioStrategy.objects.filter(user=self.request.user)


class StrategyEstimateView(APIView):
    def get(self, request: Request, pk: int) -> Response:
        try:
            strategy = PortfolioStrategy.objects.select_related("user").get(
                pk=pk, user=request.user
            )
        except PortfolioStrategy.DoesNotExist:
            return Response({"detail": "not found"}, status=404)
        return Response(estimate_cycle(strategy))


class StrategyRunNowView(APIView):
    def post(self, request: Request, pk: int) -> Response:
        try:
            strategy = PortfolioStrategy.objects.get(pk=pk, user=request.user)
        except PortfolioStrategy.DoesNotExist:
            return Response({"detail": "not found"}, status=404)
        as_of = request.data.get("as_of_date") or date_cls.today().isoformat()
        force = bool(request.data.get("force", False))
        result = daily_long_short_cycle.delay(strategy.pk, as_of, force=force)
        return Response(
            {"task_id": str(result.id), "status": "queued"},
            status=status.HTTP_202_ACCEPTED,
        )


class StrategyCyclesView(generics.ListAPIView):
    serializer_class = PortfolioTargetSummarySerializer

    def get_queryset(self):
        return PortfolioTarget.objects.filter(
            strategy_id=self.kwargs["pk"],
            strategy__user=self.request.user,
        ).order_by("-as_of_date", "-created_at")


class StrategyCycleDetailView(generics.RetrieveAPIView):
    serializer_class = PortfolioTargetDetailSerializer

    def get_queryset(self):
        return PortfolioTarget.objects.filter(
            strategy_id=self.kwargs["pk"],
            strategy__user=self.request.user,
        ).prefetch_related("orders", "screener_ranking")

    lookup_url_kwarg = "target_id"


class BorrowLookupView(APIView):
    def get(self, request: Request, ticker: str) -> Response:
        as_of_str = request.query_params.get("as_of")
        as_of = (
            datetime.fromisoformat(as_of_str).date()
            if as_of_str
            else date_cls.today()
        )
        info = StubBorrowProvider().quote(ticker, as_of)
        return Response({
            "ticker": info.ticker,
            "as_of_date": info.as_of_date.isoformat(),
            "is_locatable": info.is_locatable,
            "fee_pct_annual": float(info.fee_pct_annual),
            "source": info.source,
        })
