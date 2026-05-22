"""P3: Manual Book API endpoints.

All endpoints are scoped to the authenticated user's Manual Book; the
book is auto-created on first access via
``manual_book.get_or_create_manual_book``.
"""
from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal

from rest_framework import permissions
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.runs.models import Decision, Run

from .manual_book import (
    ManualBookError,
    adjust_cash,
    close_or_reduce_position,
    edit_position,
    get_or_create_manual_book,
    open_or_increase_position,
)
from .models import LedgerEntry
from .serializers import (
    LedgerEntrySerializer,
    PortfolioPreferencesSerializer,
    PortfolioValuationSerializer,
    PositionSuggestionSerializer,
)
from .suggestion import suggest_position
from .valuation import (
    get_or_create_preferences,
    invalidate_mark_cache,
    value_portfolio,
)


def _err(message: str, code: int = 400) -> Response:
    return Response({"detail": message}, status=code)


def _serialize_portfolio(portfolio) -> dict:
    valuation = value_portfolio(portfolio)
    return PortfolioValuationSerializer(asdict(valuation)).data


class PortfolioOverviewView(APIView):
    """GET /api/portfolio/  — manual-book overview (cash + valued positions)."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        try:
            portfolio = get_or_create_manual_book(request.user)
        except RuntimeError as exc:
            return _err(str(exc), code=400)
        return Response(_serialize_portfolio(portfolio))


class PortfolioPositionsView(APIView):
    """GET, POST /api/portfolio/positions/"""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        portfolio = get_or_create_manual_book(request.user)
        valuation = value_portfolio(portfolio)
        payload = PortfolioValuationSerializer(asdict(valuation)).data
        return Response(payload["positions"])

    def post(self, request: Request) -> Response:
        # Ensure the book exists for the user before mutating it.
        get_or_create_manual_book(request.user)
        data = request.data or {}
        try:
            source_run = None
            source_decision = None
            if data.get("source_run"):
                source_run = Run.objects.filter(
                    id=data["source_run"], user=request.user,
                ).first()
                if source_run is None:
                    return _err("source_run not found", 404)
            if data.get("source_decision"):
                source_decision = Decision.objects.filter(
                    id=data["source_decision"],
                ).select_related("run").first()
                if source_decision is None:
                    return _err("source_decision not found", 404)
                if source_decision.run.user_id != request.user.id:
                    return _err("source_decision belongs to another user", 403)
                if source_run is None:
                    source_run = source_decision.run
            result = open_or_increase_position(
                user=request.user,
                ticker=data.get("ticker", ""),
                side=data.get("side", ""),
                quantity=Decimal(str(data.get("quantity", "0"))),
                entry_price=Decimal(str(data.get("entry_price", "0"))),
                quantity_mode=data.get("quantity_mode", "whole"),
                source_run=source_run,
                source_decision=source_decision,
                note=data.get("note", ""),
            )
        except ManualBookError as exc:
            return _err(str(exc), exc.status_code)
        except RuntimeError as exc:
            return _err(str(exc), 400)
        valuation = value_portfolio(result.portfolio)
        return Response({
            "portfolio": PortfolioValuationSerializer(asdict(valuation)).data,
            "ledger_entry": LedgerEntrySerializer(result.ledger_entry).data,
        })


class PortfolioPositionDetailView(APIView):
    """PATCH /api/portfolio/positions/<id>/  — correction-only edit."""

    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request: Request, position_id: int) -> Response:
        data = request.data or {}
        try:
            quantity = (
                Decimal(str(data["quantity"]))
                if "quantity" in data and data["quantity"] is not None
                else None
            )
            avg_cost = (
                Decimal(str(data["avg_cost"]))
                if "avg_cost" in data and data["avg_cost"] is not None
                else None
            )
            note = data.get("note") if "note" in data else None
            result = edit_position(
                user=request.user,
                position_id=position_id,
                quantity=quantity,
                avg_cost=avg_cost,
                note=note,
                quantity_mode=data.get("quantity_mode", "whole"),
            )
        except ManualBookError as exc:
            return _err(str(exc), exc.status_code)
        valuation = value_portfolio(result.portfolio)
        return Response({
            "portfolio": PortfolioValuationSerializer(asdict(valuation)).data,
            "ledger_entry": LedgerEntrySerializer(result.ledger_entry).data,
        })


class PortfolioPositionCloseView(APIView):
    """POST /api/portfolio/positions/<id>/close/"""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request, position_id: int) -> Response:
        data = request.data or {}
        try:
            quantity_raw = data.get("quantity")
            close_qty = (
                Decimal(str(quantity_raw))
                if quantity_raw is not None and quantity_raw != ""
                else None
            )
            exit_price_raw = data.get("exit_price")
            if exit_price_raw is None or str(exit_price_raw).strip() == "":
                # Fall back to latest mark.
                from .models import Position
                from .valuation import get_mark

                pos = (
                    Position.objects.filter(id=position_id, portfolio__user=request.user)
                    .first()
                )
                if pos is None:
                    return _err("position not found", 404)
                mark = get_mark(pos.ticker, user=request.user)
                if mark is None:
                    return _err(
                        "no daily close available — pass exit_price explicitly",
                        400,
                    )
                exit_price = mark.price
            else:
                exit_price = Decimal(str(exit_price_raw))
            result = close_or_reduce_position(
                user=request.user,
                position_id=position_id,
                exit_price=exit_price,
                quantity=close_qty,
                quantity_mode=data.get("quantity_mode", "whole"),
                note=data.get("note", ""),
            )
        except ManualBookError as exc:
            return _err(str(exc), exc.status_code)
        except RuntimeError as exc:
            return _err(str(exc), 400)
        valuation = value_portfolio(result.portfolio)
        return Response({
            "portfolio": PortfolioValuationSerializer(asdict(valuation)).data,
            "ledger_entry": LedgerEntrySerializer(result.ledger_entry).data,
            "realized_pnl": str(result.realized_pnl),
        })


class PortfolioLedgerView(APIView):
    """GET /api/portfolio/ledger/  — newest first."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        portfolio = get_or_create_manual_book(request.user)
        rows = LedgerEntry.objects.filter(portfolio=portfolio).order_by(
            "-created_at", "-id",
        )[:500]
        return Response(LedgerEntrySerializer(rows, many=True).data)


class PortfolioCashView(APIView):
    """POST /api/portfolio/cash/  {kind:'deposit'|'withdrawal', amount, note?}"""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request) -> Response:
        get_or_create_manual_book(request.user)
        data = request.data or {}
        try:
            result = adjust_cash(
                user=request.user,
                kind=data.get("kind", ""),
                amount=Decimal(str(data.get("amount", "0"))),
                note=data.get("note", ""),
            )
        except ManualBookError as exc:
            return _err(str(exc), exc.status_code)
        valuation = value_portfolio(result.portfolio)
        return Response({
            "portfolio": PortfolioValuationSerializer(asdict(valuation)).data,
            "ledger_entry": LedgerEntrySerializer(result.ledger_entry).data,
        })


class PortfolioSuggestionView(APIView):
    """GET /api/portfolio/position-suggestion/

    Query params: ``run=<id>&decision=<id>&quantity_mode=whole|fractional``.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        run_id = request.query_params.get("run")
        decision_id = request.query_params.get("decision")
        quantity_mode = request.query_params.get("quantity_mode") or "whole"
        if not run_id or not decision_id:
            return _err("run and decision query parameters are required")
        try:
            decision = Decision.objects.select_related("run").get(
                id=decision_id, run_id=run_id, run__user=request.user,
            )
        except Decision.DoesNotExist:
            return _err("decision not found", 404)
        if decision.run.status != "done":
            return _err(
                f"run status is '{decision.run.status}', not 'done'",
                status_code=409,
            )

        portfolio = get_or_create_manual_book(request.user)
        try:
            suggestion = suggest_position(
                decision=decision,
                portfolio=portfolio,
                quantity_mode=quantity_mode,
            )
        except ValueError as exc:
            return _err(str(exc), 400)
        except RuntimeError as exc:
            return _err(str(exc), 400)
        return Response(PositionSuggestionSerializer(asdict(suggestion)).data)


class PortfolioPreferencesView(APIView):
    """GET, PUT /api/portfolio/preferences/  — mark cadence + interval."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        prefs = get_or_create_preferences(request.user)
        return Response(PortfolioPreferencesSerializer(prefs).data)

    def put(self, request: Request) -> Response:
        prefs = get_or_create_preferences(request.user)
        serializer = PortfolioPreferencesSerializer(
            prefs, data=request.data or {}, partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class PortfolioRefreshMarksView(APIView):
    """POST /api/portfolio/refresh-marks/

    Invalidates every open ticker's mark in Redis (across all cadences so
    a mode-switch doesn't keep serving the previous source) and returns
    the freshly-computed portfolio overview. Light rate limit of 1 call
    per 5 seconds per user to keep FMP usage sane.
    """

    permission_classes = [permissions.IsAuthenticated]

    _last_refresh_at: dict[int, float] = {}  # noqa: RUF012 - class-level rate limiter

    def post(self, request: Request) -> Response:
        import time

        now = time.monotonic()
        last = self._last_refresh_at.get(request.user.id, 0.0)
        if now - last < 5.0:
            return _err(
                "Refreshing too quickly — please wait a few seconds.",
                code=429,
            )
        self._last_refresh_at[request.user.id] = now

        portfolio = get_or_create_manual_book(request.user)
        tickers = list(
            portfolio.positions.values_list("ticker", flat=True),
        )
        invalidate_mark_cache(request.user, tickers)
        valuation = value_portfolio(portfolio)
        return Response(PortfolioValuationSerializer(asdict(valuation)).data)
