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
