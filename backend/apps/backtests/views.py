from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from hedgefund.celery import app as celery_app

from .estimator import estimate_cost
from .metrics import baseline_curve, stitched_oos_returns
from .models import Backtest, BacktestDay
from .serializers import (
    DEFAULT_UNIVERSE_20,
    BacktestCreateSerializer,
    BacktestDetailSerializer,
    BacktestListSerializer,
)
from .tasks import run_backtest


class BacktestListCreateView(generics.ListCreateAPIView):
    def get_queryset(self):
        return (
            Backtest.objects.filter(user=self.request.user)
            .select_related("metrics")
            .order_by("-created_at")
        )

    def get_serializer_class(self):
        if self.request.method == "POST":
            return BacktestCreateSerializer
        return BacktestListSerializer

    def perform_create(self, serializer: BacktestCreateSerializer) -> None:
        bt = serializer.save(user=self.request.user)
        async_result = run_backtest.delay(bt.id)
        Backtest.objects.filter(pk=bt.pk).update(celery_task_id=str(async_result.id or ""))


class BacktestDetailView(generics.RetrieveAPIView):
    serializer_class = BacktestDetailSerializer

    def get_queryset(self):
        return (
            Backtest.objects.filter(user=self.request.user)
            .select_related("metrics")
            .prefetch_related("folds")
        )


class BacktestCancelView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request, pk: int) -> Response:
        try:
            bt = Backtest.objects.get(pk=pk, user=request.user)
        except Backtest.DoesNotExist:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        if bt.status not in Backtest.ACTIVE_STATUSES:
            return Response({"detail": f"already {bt.status}"}, status=status.HTTP_409_CONFLICT)
        if bt.celery_task_id:
            celery_app.control.revoke(bt.celery_task_id, terminate=True, signal="SIGTERM")
        bt.status = Backtest.CANCELLED
        bt.error_message = "Cancelled by user."
        bt.finished_at = timezone.now()
        bt.save(update_fields=["status", "error_message", "finished_at"])
        return Response({"id": bt.pk, "status": bt.status})


class EquityCurveView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, pk: int) -> Response:
        try:
            bt = Backtest.objects.get(pk=pk, user=request.user)
        except Backtest.DoesNotExist:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        dates, equity, _ = stitched_oos_returns(bt)
        baseline = baseline_curve(bt, dates)
        # Tag each point with fold_index for client-side shading.
        fold_lookup = {d: fid for d, fid in BacktestDay.objects.filter(
            backtest=bt, segment=BacktestDay.SEG_OOS
        ).values_list("date", "fold_id")}
        points = [
            {
                "date": d.isoformat(),
                "portfolio_value": round(v, 2),
                "baseline": round(b, 2) if i < len(baseline) else None,
                "fold_id": fold_lookup.get(d),
            }
            for i, (d, v) in enumerate(zip(dates, equity, strict=False))
            for b in [baseline[i] if i < len(baseline) else None]
        ]
        return Response({"points": points, "baseline_kind": bt.baseline})


class FoldsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, pk: int) -> Response:
        try:
            bt = Backtest.objects.get(pk=pk, user=request.user)
        except Backtest.DoesNotExist:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        from .serializers import BacktestFoldSerializer
        rows = bt.folds.all()
        return Response({"folds": BacktestFoldSerializer(rows, many=True).data})


class DeflationView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, pk: int) -> Response:
        try:
            bt = Backtest.objects.get(pk=pk, user=request.user)
        except Backtest.DoesNotExist:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        m = getattr(bt, "metrics", None)
        per_fold = [
            {
                "fold_index": f.fold_index,
                "is_sharpe": float(f.is_sharpe),
                "oos_sharpe": float(f.oos_sharpe),
            }
            for f in bt.folds.all()
        ]
        return Response({
            "per_fold": per_fold,
            "mean_is_sharpe": float(m.mean_is_sharpe) if m else 0.0,
            "mean_oos_sharpe": float(m.mean_oos_sharpe) if m else 0.0,
            "sharpe_deflation": float(m.sharpe_deflation) if m else 0.0,
            "oos_sharpe_std": float(m.oos_sharpe_std) if m else 0.0,
            "red_flag": (float(m.sharpe_deflation) < 0.3) if m else False,
        })


class AttributionView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, pk: int) -> Response:
        try:
            bt = Backtest.objects.get(pk=pk, user=request.user)
        except Backtest.DoesNotExist:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        m = getattr(bt, "metrics", None)
        return Response({"per_agent": (m.per_agent_attribution if m else {})})


class BacktestEstimateView(APIView):
    """Pre-flight cost estimate — no DB write, no Celery task."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request) -> Response:
        import datetime as _dt

        d = request.data or {}
        try:
            start = _dt.date.fromisoformat(d["start_date"])
            end = _dt.date.fromisoformat(d["end_date"])
            universe = list(d.get("universe") or [])
            if not universe:
                return Response({"detail": "universe is required"}, status=400)
        except (KeyError, ValueError, TypeError) as e:
            return Response({"detail": f"bad input: {e}"}, status=400)
        est = estimate_cost(
            universe=universe,
            start_date=start,
            end_date=end,
            rebalance_frequency=d.get("rebalance_frequency", "weekly"),
            personas=d.get("personas") or None,
            model_overrides=d.get("model_overrides") or None,
            max_budget_usd=d.get("max_budget_usd", 4.00),
        )
        return Response(est)


class DefaultUniverseView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response({"universe": DEFAULT_UNIVERSE_20})


class BacktestCompareView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request) -> Response:
        a_id = request.data.get("backtest_a_id")
        b_id = request.data.get("backtest_b_id")
        try:
            a = Backtest.objects.get(pk=a_id, user=request.user)
            b = Backtest.objects.get(pk=b_id, user=request.user)
        except Backtest.DoesNotExist:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)

        def _payload(bt: Backtest) -> dict:
            dates, equity, _ = stitched_oos_returns(bt)
            return {
                "id": bt.id, "name": bt.name,
                "points": [
                    {"date": d.isoformat(), "portfolio_value": round(v, 2)}
                    for d, v in zip(dates, equity, strict=False)
                ],
                "metrics": BacktestDetailSerializer(bt).data.get("metrics"),
            }

        return Response({"a": _payload(a), "b": _payload(b)})
