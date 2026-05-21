from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime

from django.db import transaction
from django.utils import timezone
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
from .tasks import daily_long_short_cycle, dispatch_approved_cycle, estimate_cycle


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


def _normalise_subset(raw) -> list[str] | None:
    """None if the field was omitted; sorted, uppercased, de-duped list otherwise."""
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("must be a list of tickers")
    cleaned: list[str] = []
    seen: set[str] = set()
    for t in raw:
        s = str(t).strip().upper()
        if not s:
            continue
        if s in seen:
            raise ValueError(f"duplicate ticker {s!r}")
        seen.add(s)
        cleaned.append(s)
    return cleaned


class CycleApproveCouncilView(APIView):
    """P2l: approve a budget-valid subset of screener candidates and dispatch."""

    def _load_target(self, request: Request, pk: int, target_id: int):
        try:
            return PortfolioTarget.objects.select_related(
                "strategy", "screener_ranking",
            ).get(pk=target_id, strategy_id=pk, strategy__user=request.user)
        except PortfolioTarget.DoesNotExist:
            return None

    def post(self, request: Request, pk: int, target_id: int) -> Response:
        from . import runs_bridge

        target = self._load_target(request, pk, target_id)
        if target is None:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        if target.status != PortfolioTarget.AWAITING_REVIEW:
            return Response(
                {"detail": f"target status is {target.status!r}, not awaiting_review"},
                status=status.HTTP_409_CONFLICT,
            )
        if target.screener_ranking_id is None:
            return Response(
                {"detail": "target has no persisted screener_ranking"},
                status=status.HTTP_409_CONFLICT,
            )
        strategy = target.strategy
        ranking = target.screener_ranking

        try:
            long_subset = _normalise_subset(request.data.get("long_tickers"))
            short_subset = _normalise_subset(request.data.get("short_tickers"))
            sector_subset = _normalise_subset(request.data.get("sector_tickers"))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        # Sector-flavor strategies put their picks in long_candidates. Accept
        # either field as the long whitelist for ergonomics.
        is_sector_flavor = strategy.kind in (
            PortfolioStrategy.KIND_SECTOR_ROTATION,
            PortfolioStrategy.KIND_GLOBAL_MACRO,
        )
        if is_sector_flavor and sector_subset is not None and long_subset is None:
            long_subset = sector_subset

        # Validate the subsets against the persisted ranking.
        candidates, unknown = runs_bridge.candidates_from_ranking(
            ranking,
            long_subset=long_subset,
            short_subset=short_subset,
            is_sector_flavor=is_sector_flavor,
        )
        if unknown:
            return Response(
                {"detail": f"unknown tickers in approval: {unknown}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not candidates:
            return Response(
                {"detail": "approval contains no candidates"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Re-estimate cost against the actual approved set.
        cost = runs_bridge.estimate_candidates_cost(strategy, candidates)
        if cost["exceeds_ceiling"]:
            return Response(
                {
                    "detail": "approved set exceeds cost_ceiling_per_cycle_usd",
                    "estimate": cost,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Re-derive subset keys: if a side was omitted (None), pass the full
        # set from the ranking so dispatch_approved_cycle doesn't reject all.
        if long_subset is None:
            long_keys = [str(e.get("ticker", "")).upper() for e in (ranking.long_candidates or [])]
        else:
            long_keys = long_subset
        if short_subset is None:
            short_keys = [
                str(e.get("ticker", "")).upper()
                for e in (ranking.short_candidates or [])
            ]
        else:
            short_keys = short_subset

        result = dispatch_approved_cycle(
            strategy=strategy,
            target=target,
            approved_longs=long_keys,
            approved_shorts=short_keys,
        )
        result["estimate"] = cost
        return Response(result, status=status.HTTP_202_ACCEPTED)


class CycleRejectView(APIView):
    """P2l: cancel a target in awaiting_review / running_council / constructing.

    Marks the target as cancelled, revokes the chord callback and any active
    candidate runs where we know the celery_task_id. Same-day reruns are
    unblocked because the partial unique constraint excludes cancelled rows.
    """

    def post(self, request: Request, pk: int, target_id: int) -> Response:
        from apps.runs.models import Run
        from hedgefund.celery import app as celery_app

        try:
            target = PortfolioTarget.objects.select_related("strategy").get(
                pk=target_id, strategy_id=pk, strategy__user=request.user,
            )
        except PortfolioTarget.DoesNotExist:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        if target.status not in (
            PortfolioTarget.AWAITING_REVIEW,
            PortfolioTarget.RUNNING_COUNCIL,
            PortfolioTarget.CONSTRUCTING,
            PortfolioTarget.SCREENING,
            PortfolioTarget.RUNNING,
            PortfolioTarget.QUEUED,
        ):
            return Response(
                {"detail": f"target status is {target.status!r}; cannot cancel"},
                status=status.HTTP_409_CONFLICT,
            )

        with transaction.atomic():
            # Revoke the chord callback. Strategies in running_council /
            # constructing have a callback task_id stored on the target.
            if target.celery_task_id:
                try:
                    celery_app.control.revoke(
                        target.celery_task_id, terminate=True, signal="SIGTERM",
                    )
                except Exception:  # broker reachability issues — keep cancelling
                    pass

            # Mark every queued / running candidate run cancelled, revoking
            # their celery tasks too. SET_NULL on Run.portfolio_target means
            # this works even if Django couldn't load the target row first.
            active = list(Run.objects.filter(
                portfolio_target=target,
                status__in=tuple(Run.ACTIVE_STATUSES),
            ))
            for r in active:
                if r.celery_task_id:
                    try:
                        celery_app.control.revoke(
                            r.celery_task_id, terminate=True, signal="SIGTERM",
                        )
                    except Exception:
                        pass
            cancelled_count = Run.objects.filter(
                pk__in=[r.pk for r in active]
            ).update(
                status=Run.CANCELLED,
                error_message="Cancelled because parent cycle was rejected.",
                finished_at=timezone.now(),
            )

            target.status = PortfolioTarget.CANCELLED
            target.error_message = "Cancelled by user."
            target.finished_at = timezone.now()
            target.save(update_fields=["status", "error_message", "finished_at"])

        return Response({
            "target_id": target.pk,
            "status": target.status,
            "cancelled_runs": cancelled_count,
        })
