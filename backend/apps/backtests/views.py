import logging

from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from hedgefund.celery import app as celery_app
from hedgefund.pagination import DefaultPageNumberPagination

from .estimator import estimate_cost
from .metrics import (
    BENCHMARK_TICKERS,
    baseline_curve,
    benchmark_curve,
    rolling_sharpe_series,
    stitched_oos_returns,
)
from .models import Backtest, BacktestDay
from .serializers import (
    DEFAULT_UNIVERSE_20,
    REBALANCE_FREQUENCIES,
    BacktestCreateSerializer,
    BacktestDetailSerializer,
    BacktestListSerializer,
)
from .tasks import run_backtest

logger = logging.getLogger(__name__)


class BacktestListCreateView(generics.ListCreateAPIView):
    # P10 §D4: done/failed backtests can never be deleted (protected history),
    # so the list is append-only — paginate at 50 and hide archived rows by
    # default (?include_archived=1 to see them).
    pagination_class = DefaultPageNumberPagination

    def get_queryset(self):
        qs = (
            Backtest.objects.filter(user=self.request.user)
            .select_related("metrics")
            .order_by("-created_at")
        )
        include_archived = self.request.query_params.get("include_archived") in (
            "1", "true", "yes",
        )
        if not include_archived:
            qs = qs.filter(archived_at__isnull=True)
        return qs

    def get_serializer_class(self):
        if self.request.method == "POST":
            return BacktestCreateSerializer
        return BacktestListSerializer

    def perform_create(self, serializer: BacktestCreateSerializer) -> None:
        bt = serializer.save(user=self.request.user)
        async_result = run_backtest.delay(bt.id)
        Backtest.objects.filter(pk=bt.pk).update(celery_task_id=str(async_result.id or ""))


class BacktestArchiveView(APIView):
    """P10 §D4 — soft archive/unarchive (the graphs pattern). POST
    /backtests/<pk>/archive/ {archived: true|false}. Any terminal status may be
    archived; active runs must be cancelled first."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request, pk: int) -> Response:
        try:
            bt = Backtest.objects.get(pk=pk, user=request.user)
        except Backtest.DoesNotExist:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        if bt.status in Backtest.ACTIVE_STATUSES:
            return Response(
                {"detail": f"backtest is {bt.status}; cancel it before archiving"},
                status=status.HTTP_409_CONFLICT,
            )
        archived = bool(request.data.get("archived", True))
        bt.archived_at = timezone.now() if archived else None
        bt.save(update_fields=["archived_at"])
        return Response({
            "id": bt.pk,
            "archived_at": bt.archived_at.isoformat() if bt.archived_at else None,
        })


class BacktestDetailView(generics.RetrieveDestroyAPIView):
    serializer_class = BacktestDetailSerializer

    # P4 WS-D: only cancelled / aborted / synthetic backtests may be deleted.
    # done and failed are the audit trail of which configs were tried — never
    # deletable; active ones must be cancelled first.
    DELETABLE_STATUSES = frozenset({
        Backtest.CANCELLED,
        Backtest.ABORTED_BUDGET,
        Backtest.ABORTED_PARTIAL,
        Backtest.SYNTHETIC,
    })

    def get_queryset(self):
        return (
            Backtest.objects.filter(user=self.request.user)
            .select_related("metrics")
            .prefetch_related("folds")
        )

    def destroy(self, request: Request, *args, **kwargs):
        bt = self.get_object()
        if bt.status in Backtest.ACTIVE_STATUSES:
            logger.warning(
                "backtest_delete_refused kind=active id=%s status=%s user_id=%s",
                bt.pk, bt.status, request.user.id,
            )
            return Response(
                {"detail": f"backtest is {bt.status}; cancel it before deleting"},
                status=status.HTTP_409_CONFLICT,
            )
        if bt.status not in self.DELETABLE_STATUSES:
            logger.warning(
                "backtest_delete_refused kind=protected id=%s status=%s user_id=%s",
                bt.pk, bt.status, request.user.id,
            )
            return Response(
                {"detail": f"{bt.status} backtests are protected history and "
                           "cannot be deleted"},
                status=status.HTTP_409_CONFLICT,
            )
        # Folds / days / metrics all cascade via on_delete=CASCADE.
        bt_id, bt_status = bt.pk, bt.status
        bt.delete()
        logger.info("backtest_deleted id=%s status=%s user_id=%s",
                    bt_id, bt_status, request.user.id)
        return Response(status=status.HTTP_204_NO_CONTENT)


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
            # terminate=True SIGTERMs the child that is already executing;
            # revoking by id also covers the still-queued case.
            celery_app.control.revoke(bt.celery_task_id, terminate=True, signal="SIGTERM")
        # Conditional update: only flip a row that is STILL active. Without the
        # filter a cancel racing the worker's own terminal write (DONE / FAILED /
        # ABORTED_*) would clobber it, and the run's real outcome would be lost.
        updated = Backtest.objects.filter(
            pk=bt.pk, user=request.user, status__in=Backtest.ACTIVE_STATUSES
        ).update(
            status=Backtest.CANCELLED,
            error_message="Cancelled by user.",
            finished_at=timezone.now(),
        )
        bt.refresh_from_db(fields=["status"])
        if not updated:
            return Response({"detail": f"already {bt.status}"}, status=status.HTTP_409_CONFLICT)
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
        # P10 §B2: SPY-TR / QQQ-TR overlays (omitted when no bars in-window)
        # + the rolling ~3y Sharpe sparkline series.
        base = float(equity[0]) if equity else float(bt.starting_cash)
        bench = {
            t: c for t in BENCHMARK_TICKERS
            if (c := benchmark_curve(t, dates, base))
        }
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
                **{
                    t.lower(): round(curve[i], 2)
                    for t, curve in bench.items() if i < len(curve)
                },
            }
            for i, (d, v) in enumerate(zip(dates, equity, strict=False))
            for b in [baseline[i] if i < len(baseline) else None]
        ]
        return Response({
            "points": points,
            "baseline_kind": bt.baseline,
            "benchmarks": sorted(bench.keys()),
            "rolling_sharpe": rolling_sharpe_series(dates, equity),
        })


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
        meaningful = bt.deflation_meaningful  # P10 §B4
        return Response({
            "per_fold": per_fold,
            "mean_is_sharpe": float(m.mean_is_sharpe) if m else 0.0,
            "mean_oos_sharpe": float(m.mean_oos_sharpe) if m else 0.0,
            "sharpe_deflation": float(m.sharpe_deflation) if m else 0.0,
            "oos_sharpe_std": float(m.oos_sharpe_std) if m else 0.0,
            "deflation_meaningful": meaningful,
            "red_flag": (float(m.sharpe_deflation) < 0.3) if (m and meaningful) else False,
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

        d = request.data if isinstance(request.data, dict) else {}
        # Everything below used to reach estimate_cost unchecked, so a plausible
        # typo ("max_budget_usd": "four dollars", or model_overrides sent as a
        # string) surfaced as a 500 with a stack trace instead of a 400 naming
        # the field.
        try:
            start = _dt.date.fromisoformat(d["start_date"])
            end = _dt.date.fromisoformat(d["end_date"])
            raw_universe = d.get("universe") or []
            if not isinstance(raw_universe, list):
                return Response({"detail": "universe must be a list of tickers"}, status=400)
            universe = [str(t).upper() for t in raw_universe if str(t).strip()]
            if not universe:
                return Response({"detail": "universe is required"}, status=400)
            freq = d.get("rebalance_frequency", "weekly")
            if freq not in REBALANCE_FREQUENCIES:
                return Response(
                    {"detail": f"rebalance_frequency must be one of "
                               f"{sorted(REBALANCE_FREQUENCIES)}"},
                    status=400,
                )
            personas = d.get("personas") or None
            if personas is not None and (
                not isinstance(personas, list)
                or not all(isinstance(p, str) for p in personas)
            ):
                return Response({"detail": "personas must be a list of names"}, status=400)
            overrides = d.get("model_overrides") or None
            if overrides is not None:
                if not isinstance(overrides, dict):
                    return Response(
                        {"detail": "model_overrides must be an object mapping "
                                   "agent -> 'provider:model'"},
                        status=400,
                    )
                bad = sorted(k for k, v in overrides.items() if not isinstance(v, str))
                if bad:
                    return Response(
                        {"detail": f"model_overrides values must be "
                                   f"'provider:model' strings; bad keys: {bad}"},
                        status=400,
                    )
            budget = d.get("max_budget_usd", 4.00)
            budget = float(budget) if budget is not None else None
        except (KeyError, ValueError, TypeError, AttributeError) as e:
            return Response({"detail": f"bad input: {e}"}, status=400)
        try:
            est = estimate_cost(
                universe=universe,
                start_date=start,
                end_date=end,
                rebalance_frequency=freq,
                personas=personas,
                model_overrides=overrides,
                max_budget_usd=budget,
                disable_cio=bool(d.get("disable_cio", True)),
            )
        except (ValueError, TypeError, AttributeError) as e:
            logger.warning("backtest_estimate_bad_input user_id=%s err=%s", request.user.id, e)
            return Response({"detail": f"bad input: {e}"}, status=400)
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
            # Both ids must be the CALLER's own rows — a foreign id is a 404, the
            # same as fetching it directly, so compare can't be used to read
            # another user's curve or metrics.
            a = Backtest.objects.get(pk=a_id, user=request.user)
            b = Backtest.objects.get(pk=b_id, user=request.user)
        except (Backtest.DoesNotExist, TypeError, ValueError):
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        not_done = [bt.pk for bt in (a, b) if bt.status != Backtest.DONE]
        if not_done:
            # A queued/running/failed row has no stitched curve and no metrics;
            # comparing against it silently renders an empty side.
            return Response(
                {"detail": f"both backtests must be done; {not_done} are not"},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
