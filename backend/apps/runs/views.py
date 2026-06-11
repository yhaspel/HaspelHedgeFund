import datetime as dt
import logging

from django.conf import settings
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from hedgefund.celery import app as celery_app
from hedgefund.pagination import DefaultPageNumberPagination
from hedgefund_agents.registry import MODEL_CATALOG

from .models import Run
from .serializers import (
    RunCreateSerializer,
    RunDetailSerializer,
    RunListSerializer,
)
from .tasks import execute_run

logger = logging.getLogger(__name__)


class RunListCreateView(generics.ListCreateAPIView):
    # P10 §D4: runs are unbounded (386 rows shipped in one response at audit
    # time) — paginate at 50 and default the window to the last 30 days
    # (?days=N to widen, ?days=all for everything).
    pagination_class = DefaultPageNumberPagination
    DEFAULT_WINDOW_DAYS = 30

    def get_queryset(self):
        qs = Run.objects.filter(user=self.request.user)
        days_raw = (self.request.query_params.get("days") or "").lower()
        if days_raw != "all":
            try:
                days = int(days_raw) if days_raw else self.DEFAULT_WINDOW_DAYS
            except (TypeError, ValueError):
                days = self.DEFAULT_WINDOW_DAYS
            qs = qs.filter(created_at__gte=timezone.now() - dt.timedelta(days=days))
        # P2l: optional source filter. "all" or missing = no filter.
        source = (self.request.query_params.get("source") or "").lower()
        if source in (Run.ADHOC, Run.STRATEGY):
            qs = qs.filter(source=source)
        target_id = self.request.query_params.get("portfolio_target")
        if target_id:
            try:
                qs = qs.filter(portfolio_target_id=int(target_id))
            except (TypeError, ValueError):
                pass
        # P3b: full-text transcript search. Postgres uses a tsvector query
        # (backed by the GIN index in migration 0010); sqlite (tests) falls back
        # to a substring match.
        search = (self.request.query_params.get("search") or "").strip()
        if search:
            from django.db import connection

            if connection.vendor == "postgresql":
                from django.contrib.postgres.search import SearchQuery, SearchVector

                qs = qs.annotate(
                    _sv=SearchVector("search_text", config="english")
                ).filter(_sv=SearchQuery(search, config="english"))
            else:
                qs = qs.filter(search_text__icontains=search)
        return (
            qs.select_related("portfolio_target__strategy")
            .prefetch_related("decisions")
            .order_by("-created_at")
        )

    def get_serializer_class(self):
        return RunCreateSerializer if self.request.method == "POST" else RunListSerializer

    def perform_create(self, serializer: RunCreateSerializer) -> None:
        run = serializer.save(user=self.request.user)
        async_result = execute_run.delay(run.id)
        Run.objects.filter(pk=run.pk).update(celery_task_id=str(async_result.id or ""))


class RunDetailView(generics.RetrieveAPIView):
    serializer_class = RunDetailSerializer

    def get_queryset(self):
        return (
            Run.objects.filter(user=self.request.user)
            .select_related("portfolio_target__strategy")
            .prefetch_related("messages", "decisions", "llm_calls")
        )


class RunCancelView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request, pk: int) -> Response:
        try:
            run = Run.objects.get(pk=pk, user=request.user)
        except Run.DoesNotExist:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        if run.status not in Run.ACTIVE_STATUSES:
            return Response(
                {"detail": f"run already {run.status}"},
                status=status.HTTP_409_CONFLICT,
            )
        if run.celery_task_id:
            # SIGTERM the prefork subprocess executing this task. The worker's
            # task wrapper catches the resulting exception and the Run row is
            # already marked cancelled below — the persisted state wins.
            celery_app.control.revoke(
                run.celery_task_id, terminate=True, signal="SIGTERM"
            )
        run.status = Run.CANCELLED
        run.error_message = "Cancelled by user."
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error_message", "finished_at"])
        return Response({"id": run.pk, "status": run.status})


class RunRerunView(APIView):
    """P4 WS-A: rerun a terminal Analysis run.

    Creates a NEW Run row copying the original's payload (tickers,
    model_overrides, as_of_date, personas, source, portfolio_target) and
    links it back via ``rerun_of`` so the original is never mutated — the
    failed/cancelled row stays as evidence. A strategy-sourced rerun keeps
    ``source="strategy"`` and its cycle back-link. The new run re-resolves
    providers against the user's *current* keys.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request, pk: int) -> Response:
        try:
            original = Run.objects.get(pk=pk, user=request.user)
        except Run.DoesNotExist:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        if original.status in Run.ACTIVE_STATUSES:
            return Response(
                {"detail": f"run is still {original.status}; cancel or wait for it to finish"},
                status=status.HTTP_409_CONFLICT,
            )
        # Copy the request payload verbatim. personas is preserved as-is
        # (empty list = "all", a meaning execute_run relies on). portfolio_target
        # may already be NULL if the original cycle was cancelled/deleted; that
        # is fine — the run stays source="strategy" but loses the back-link.
        new_run = Run.objects.create(
            user=request.user,
            tickers=list(original.tickers or []),
            model_overrides=dict(original.model_overrides or {}),
            as_of_date=original.as_of_date,
            personas=list(original.personas or []),
            source=original.source,
            portfolio_target_id=original.portfolio_target_id,
            rerun_of=original,
        )
        async_result = execute_run.delay(new_run.id)
        Run.objects.filter(pk=new_run.pk).update(
            celery_task_id=str(async_result.id or "")
        )
        logger.info(
            "run_rerun original_id=%s new_id=%s user_id=%s",
            original.pk, new_run.pk, request.user.id,
        )
        return Response(
            {"id": new_run.pk, "status": new_run.status, "rerun_of": original.pk},
            status=status.HTTP_201_CREATED,
        )


class ModelCatalogView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response({"models": MODEL_CATALOG})


def _provider_state(key_setting: str) -> str:
    """Map a settings key value to a UI-visible state label."""
    return "configured" if getattr(settings, key_setting, "") else "missing"


def _latest_freshness(model_cls, *, date_field: str, scope: dict | None = None) -> dict:
    """Return as_of/age for the most recent row of a data model."""
    try:
        qs = model_cls.objects.all()
        if scope:
            qs = qs.filter(**scope)
        latest = qs.order_by(f"-{date_field}").first()
        if latest is None:
            return {"last_at": None, "age_days": None, "count": 0}
        val = getattr(latest, date_field)
        if isinstance(val, dt.datetime):
            age = (timezone.now() - val).days
            iso = val.isoformat()
        else:
            age = (dt.date.today() - val).days
            iso = val.isoformat()
        return {
            "last_at": iso,
            "age_days": int(age),
            "count": qs.count(),
        }
    except Exception as e:  # pragma: no cover — defensive
        return {"last_at": None, "age_days": None, "error": str(e)[:100]}


class ProviderDiagnosticsView(APIView):
    """P01/P02b review: operator-facing provider state.

    Reports configured/missing key state for every provider, the most
    recent persisted row per provider, and the current run cap settings.
    Read-only; never hits the wire.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        from apps.data.models import (
            DailyBar,
            Fundamental,
            MacroSnapshot,
            NewsItem,
        )

        # Per-user BYOK status: do they have a key row at all?
        user_byok: dict[str, bool] = {}
        try:
            from apps.models_catalog.models import ProviderKey

            pk = ProviderKey.objects.filter(user_id=request.user.id).first()
            for prov in ("fmp", "tiingo", "fred", "anthropic", "openrouter"):
                user_byok[prov] = bool(pk and pk.has_key(prov))
        except Exception:
            user_byok = {}

        return Response(
            {
                "as_of": timezone.now().isoformat(),
                "providers": {
                    "fmp": {
                        "key": _provider_state("FMP_API_KEY"),
                        "user_byok": user_byok.get("fmp", False),
                        "freshness": _latest_freshness(
                            DailyBar, date_field="fetched_at",
                            scope={"source": "fmp"},
                        ),
                    },
                    "tiingo": {
                        "key": _provider_state("TIINGO_API_KEY"),
                        "user_byok": user_byok.get("tiingo", False),
                        "freshness": _latest_freshness(
                            NewsItem, date_field="fetched_at",
                            scope={"provider": "tiingo"},
                        ),
                    },
                    "fred": {
                        "key": _provider_state("FRED_API_KEY"),
                        "user_byok": user_byok.get("fred", False),
                        "freshness": _latest_freshness(
                            MacroSnapshot, date_field="created_at",
                        ),
                    },
                    "edgar": {
                        "key": "configured",  # public, no key
                        "user_byok": False,
                        "freshness": _latest_freshness(
                            Fundamental, date_field="fetched_at",
                            scope={"source": "edgar"},
                        ),
                    },
                    "anthropic": {
                        "key": _provider_state("ANTHROPIC_API_KEY"),
                        "user_byok": user_byok.get("anthropic", False),
                    },
                    "openrouter": {
                        "key": _provider_state("OPENROUTER_API_KEY"),
                        "user_byok": user_byok.get("openrouter", False),
                    },
                },
                "policy": {
                    "allow_platform_data_keys": bool(
                        getattr(settings, "ALLOW_PLATFORM_DATA_KEYS", False)
                    ),
                },
            }
        )
