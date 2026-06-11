"""P7 §13 — fund-level API: the 3-account rollup + the fund kill switch."""
from __future__ import annotations

from rest_framework import permissions
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from . import fund as fund_layer
from .models import AutonomousFund


def _user_fund(user) -> AutonomousFund | None:
    return AutonomousFund.objects.filter(owner=user).order_by("id").first()


class FundOverviewView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        fund = _user_fund(request.user)
        if fund is None:
            return Response({"fund": None})
        return Response(fund_layer.fund_overview(fund))


class FundCompositeView(APIView):
    """P10 §B5 — GET /api/fund/composite/ — the pods' stitched OOS validation
    curves combined at configurable weights vs SPY-TR/QQQ-TR.

    ``?weights=53:0.6,54:0.2,55:0.2`` (strategy-id:weight, normalized; default
    equal weight — the live 33/33/33 capital split)."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        from .fund_composite import fund_composite

        fund = _user_fund(request.user)
        if fund is None:
            return Response({"available": False, "reason": "no fund"})
        return Response(
            fund_composite(fund, weights_raw=request.query_params.get("weights"))
        )


class FundHistoryView(APIView):
    """P10 §C2 — GET /api/fund/history/?days=N — per-account + aggregate NAV
    history from PortfolioSnapshot, with time-weighted (flow-adjusted) return
    indices and normalized SPY/QQQ overlays."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        from .snapshots import fund_history

        fund = _user_fund(request.user)
        if fund is None:
            return Response({"available": False, "reason": "no fund"})
        days_raw = request.query_params.get("days")
        try:
            days = int(days_raw) if days_raw else None
        except (TypeError, ValueError):
            days = None
        return Response(fund_history(fund, days=days))


class FundHaltView(APIView):
    """Fund-level kill switch — halts all member accounts at once (§7/§10)."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request) -> Response:
        fund = _user_fund(request.user)
        if fund is None:
            return Response({"detail": "no fund"}, status=404)
        n = fund_layer.halt_fund(fund, reason="manual_kill_switch")
        return Response({"state": fund.state, "accounts_halted": n})


class FundResumeView(APIView):
    """Clear the fund halt. Per-account halts are NOT auto-cleared (fail-safe)."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request) -> Response:
        fund = _user_fund(request.user)
        if fund is None:
            return Response({"detail": "no fund"}, status=404)
        fund_layer.resume_fund(fund)
        return Response({"state": fund.state})
