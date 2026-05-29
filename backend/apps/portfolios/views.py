from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import datetime
from decimal import Decimal

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

logger = logging.getLogger(__name__)


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


class PortfolioHubView(APIView):
    """GET /api/portfolios/hub/ — every book the user owns in one list.

    Unifies the three portfolio kinds (manual / broker / strategy) so the
    user has a single place to see all their books. Each row links back to
    the surface that actually manages that kind of book — the manual book
    page, a broker account, or a strategy.
    """

    def get(self, request: Request) -> Response:
        from .manual_book import get_or_create_manual_book

        # Guarantee the manual book exists so it always appears on the hub.
        get_or_create_manual_book(request.user)

        portfolios = list(
            Portfolio.objects.filter(user=request.user).prefetch_related(
                "positions",
            )
        )

        # A Portfolio row alone does not know which broker account or
        # strategy owns it — build the reverse lookups once.
        from apps.brokers.capabilities import get_capabilities
        from apps.brokers.models import BrokerAccount

        broker_by_pf = {
            ba.portfolio_id: ba
            for ba in BrokerAccount.objects.filter(user=request.user)
        }
        strat_by_pf: dict[int, PortfolioStrategy] = {}
        for st in PortfolioStrategy.objects.filter(user=request.user):
            strat_by_pf.setdefault(st.portfolio_id, st)

        kind_order = {
            Portfolio.KIND_MANUAL: 0,
            Portfolio.KIND_BROKER: 1,
            Portfolio.KIND_STRATEGY: 2,
        }
        books: list[dict] = []
        for p in portfolios:
            positions = list(p.positions.all())
            market_value = sum(
                (pos.quantity * pos.avg_cost for pos in positions),
                Decimal("0"),
            )
            cash = p.cash_balance
            book = {
                "kind": p.kind,
                "portfolio_id": p.id,
                "name": p.name,
                "subtitle": "",
                "cash": str(cash),
                "market_value": str(market_value),
                "equity": str(cash + market_value),
                "positions_count": len(positions),
                "link_route": "",
                "status": "",
            }
            if p.kind == Portfolio.KIND_MANUAL:
                book["name"] = "Manual book"
                book["subtitle"] = "Hand-managed positions & cash"
                book["link_route"] = "/portfolio"
            elif p.kind == Portfolio.KIND_BROKER:
                ba = broker_by_pf.get(p.id)
                if ba is not None:
                    cap = get_capabilities(ba.broker)
                    display = cap.display_name if cap else ba.broker
                    book["name"] = ba.label
                    book["subtitle"] = f"{display} · {ba.mode}"
                    book["link_route"] = f"/broker-accounts/{ba.id}"
                    book["status"] = ba.connection_status
                else:
                    book["subtitle"] = "Broker book"
                    book["link_route"] = "/broker-accounts"
            elif p.kind == Portfolio.KIND_STRATEGY:
                st = strat_by_pf.get(p.id)
                if st is not None:
                    book["name"] = st.name
                    book["subtitle"] = st.get_kind_display()
                    book["link_route"] = f"/strategies/{st.id}"
                else:
                    book["subtitle"] = "Strategy book"
                    book["link_route"] = "/strategies"
            books.append(book)

        books.sort(
            key=lambda b: (kind_order.get(b["kind"], 9), b["name"].lower()),
        )
        totals = {
            "books": len(books),
            "cash": str(sum((Decimal(b["cash"]) for b in books), Decimal("0"))),
            "equity": str(
                sum((Decimal(b["equity"]) for b in books), Decimal("0")),
            ),
        }
        return Response({"books": books, "totals": totals})


class PositionsView(generics.ListAPIView):
    serializer_class = PositionSerializer

    def get_queryset(self):
        return Position.objects.filter(
            portfolio__user=self.request.user, portfolio_id=self.kwargs["portfolio_id"]
        ).order_by("ticker")


def _strategies_with_active_count(user):
    """Strategies annotated with their non-cancelled cycle count so the
    serializer's targets_count_active field (P4 WS-C delete gate) avoids N+1."""
    from django.db.models import Count, Q

    return PortfolioStrategy.objects.filter(user=user).annotate(
        targets_count_active_annotated=Count(
            "targets", filter=~Q(targets__status="cancelled"),
        ),
    )


class StrategyListCreateView(generics.ListCreateAPIView):
    serializer_class = StrategySerializer

    def get_queryset(self):
        return _strategies_with_active_count(self.request.user).order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class StrategyDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = StrategySerializer

    def get_queryset(self):
        return _strategies_with_active_count(self.request.user)

    def destroy(self, request: Request, *args, **kwargs):
        """P4 WS-C: delete a strategy only if it has no non-cancelled cycles
        and its strategy-portfolio book is empty (a freshly-seeded book with
        zero positions and zero ledger entries is detached + deleted; a book
        with history is refused with 409 so it is never silently destroyed)."""
        strategy = self.get_object()
        if strategy.targets.exclude(status=PortfolioTarget.CANCELLED).exists():
            logger.warning(
                "strategy_delete_refused kind=has_active_cycles id=%s user_id=%s",
                strategy.pk, request.user.id,
            )
            return Response(
                {"detail": "strategy has non-cancelled cycles; cancel/deactivate "
                           "instead of deleting so history is preserved"},
                status=status.HTTP_409_CONFLICT,
            )
        portfolio = strategy.portfolio
        has_positions = Position.objects.filter(portfolio=portfolio).exists()
        has_ledger = portfolio.ledger.exists()
        if has_positions or has_ledger:
            logger.warning(
                "strategy_delete_refused kind=nonempty_portfolio id=%s "
                "portfolio_id=%s user_id=%s",
                strategy.pk, portfolio.pk, request.user.id,
            )
            return Response(
                {"detail": "the strategy's portfolio has positions or ledger "
                           "history; reassign or delete that portfolio first"},
                status=status.HTTP_409_CONFLICT,
            )
        with transaction.atomic():
            strategy.delete()  # cascades cancelled targets + rankings + pairs
            portfolio.delete()  # freshly-seeded, untouched book
        logger.info(
            "strategy_deleted id=%s portfolio_id=%s user_id=%s",
            strategy.pk, portfolio.pk, request.user.id,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


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

    def retrieve(self, request, *args, **kwargs):
        # P3 addendum: lazily compute the per-cycle marked snapshot on
        # done targets, so the cycle detail page always renders an
        # up-to-date mark-to-market without a separate request. Skip for
        # active/cancelled cycles where the snapshot would be noise.
        from .cycle_mark import ensure_cycle_snapshot

        target = self.get_object()
        if target.status == PortfolioTarget.DONE:
            try:
                ensure_cycle_snapshot(target)
            except Exception:  # never block the detail render
                pass
        return super().retrieve(request, *args, **kwargs)


class StrategyCycleRefreshMarkView(APIView):
    """POST /api/strategies/<pk>/cycles/<target_id>/refresh-mark/

    Force-recompute the marked snapshot for a cycle. Used by the cycle
    detail page's "Refresh mark" affordance. Rate-limited soft cap of
    1 call / 5 s / user via an in-process timestamp dict.
    """

    permission_classes = [permissions.IsAuthenticated]

    _last_refresh_at: dict[int, float] = {}  # noqa: RUF012

    def post(self, request: Request, pk: int, target_id: int) -> Response:
        import time

        from .cycle_mark import ensure_cycle_snapshot

        now = time.monotonic()
        # `last is None` ⇒ "this user has never refreshed in this process".
        # See data/views.py for the news variant of the same bug.
        last = self._last_refresh_at.get(request.user.id)
        if last is not None and now - last < 5.0:
            return Response(
                {"detail": "Refreshing too quickly — please wait a few seconds."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        self._last_refresh_at[request.user.id] = now
        try:
            target = PortfolioTarget.objects.get(
                pk=target_id, strategy_id=pk, strategy__user=request.user,
            )
        except PortfolioTarget.DoesNotExist:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        snapshot = ensure_cycle_snapshot(target, force=True)
        return Response(snapshot)


class StrategyCycleRerunView(APIView):
    """P4 WS-B: rerun a terminal (failed/cancelled) strategy cycle.

    Dispatches a fresh cycle for the SAME as_of_date. The old terminal row is
    left in place and gets ``superseded_by`` stamped at it once the new row is
    created inside the task, so the cycles list shows a "↻ superseded" pill.
    Done cycles are refused (use Run now with force to re-evaluate a success).
    The rerun re-runs the screener for the as_of date (reproducible) rather
    than splicing the old ranking; for manual-gate strategies it lands back in
    awaiting_review for re-approval.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request, pk: int, target_id: int) -> Response:
        try:
            target = PortfolioTarget.objects.select_related("strategy").get(
                pk=target_id, strategy_id=pk, strategy__user=request.user,
            )
        except PortfolioTarget.DoesNotExist:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        if target.status in PortfolioTarget.ACTIVE_STATUSES:
            return Response(
                {"detail": f"cycle is still {target.status}; cancel or wait first"},
                status=status.HTTP_409_CONFLICT,
            )
        if target.status == PortfolioTarget.DONE:
            return Response(
                {"detail": "cannot rerun a done cycle; use Run now with force=true "
                           "to re-evaluate a successful cycle"},
                status=status.HTTP_409_CONFLICT,
            )
        # Eligible: failed or cancelled.
        result = daily_long_short_cycle.delay(
            target.strategy_id,
            target.as_of_date.isoformat(),
            force=True,
            supersedes_target_id=target.pk,
        )
        logger.info(
            "cycle_rerun original_id=%s strategy_id=%s user_id=%s as_of=%s",
            target.pk, target.strategy_id, request.user.id, target.as_of_date,
        )
        return Response(
            {
                "task_id": str(result.id),
                "new_target_pending": True,
                "superseded_target_id": target.pk,
            },
            status=status.HTTP_202_ACCEPTED,
        )


def _enrollment_to_dict(result) -> dict:
    """JSON-safe serialization of an EnrollmentResult (Decimals → str)."""
    def row_dict(r) -> dict:
        return {
            "ticker": r.ticker,
            "side": r.side,
            "target_weight_pct": r.target_weight_pct,
            "target_notional_usd": str(r.target_notional_usd),
            "suggested_quantity": str(r.suggested_quantity),
            "mark_price": str(r.mark_price) if r.mark_price is not None else None,
            "mark_source": r.mark_source,
            "current_quantity": str(r.current_quantity),
            "current_avg_cost": str(r.current_avg_cost) if r.current_avg_cost is not None else None,
            "action": r.action,
            "quantity_delta": str(r.quantity_delta),
            "rebalance_order_id": r.rebalance_order_id,
            "source_run_id": r.source_run_id,
            "source_decision_id": r.source_decision_id,
            "warnings": r.warnings,
        }

    return {
        "target_id": result.target_id,
        "as_of_date": result.as_of_date,
        "portfolio": result.portfolio,
        "rows": [row_dict(r) for r in result.rows],
        "totals": result.totals,
        "enrolled": result.enrolled,
    }


class StrategyEnrollView(APIView):
    """P4 WS-E: preview (GET) and apply (POST) a done cycle's target_weights
    into the strategy portfolio as real Position rows + ledger entries."""

    permission_classes = [permissions.IsAuthenticated]

    def _load_target(self, request: Request, pk: int, target_id: int):
        return (
            PortfolioTarget.objects.select_related("strategy", "strategy__portfolio")
            .filter(pk=target_id, strategy_id=pk, strategy__user=request.user)
            .first()
        )

    def get(self, request: Request, pk: int, target_id: int) -> Response:
        from . import runs_bridge

        target = self._load_target(request, pk, target_id)
        if target is None:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        if target.status != PortfolioTarget.DONE:
            return Response(
                {"detail": f"cycle is {target.status}, not done; nothing to enroll"},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            result = runs_bridge.preview_enrollment(target, user=request.user)
        except runs_bridge.EnrollmentError as exc:
            return Response({"detail": str(exc)}, status=exc.status_code)
        return Response(_enrollment_to_dict(result))

    def post(self, request: Request, pk: int, target_id: int) -> Response:
        from . import runs_bridge

        target = self._load_target(request, pk, target_id)
        if target is None:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        mode = (request.data.get("mode") or "").lower()
        if mode == "manual" and request.data.get("approved_tickers") is None:
            return Response(
                {"detail": "approved_tickers is required in manual mode"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = runs_bridge.enroll_target_into_portfolio(
                target,
                mode=mode,
                approved_tickers=request.data.get("approved_tickers"),
                override_quantities=request.data.get("override_quantities"),
                note=request.data.get("note", "") or "",
            )
        except runs_bridge.EnrollmentError as exc:
            return Response({"detail": str(exc)}, status=exc.status_code)
        return Response(_enrollment_to_dict(result), status=status.HTTP_201_CREATED)


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
