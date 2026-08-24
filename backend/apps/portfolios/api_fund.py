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
    """P10 §B5 / P11 A3 — GET /api/fund/composite/ — the pods' stitched OOS
    validation curves combined at configurable weights vs SPY-TR/QQQ-TR.

    ``?weights=53:0.6,54:0.2,55:0.2`` (strategy-id:weight, normalized; default
    equal weight — the live 33/33/33 capital split).
    ``?leverage=1.5`` re-levers the composite toward market risk net of a
    ``(L-1)·rf`` drag (``?financing_bps=200`` overrides the ~2%/yr default).
    ``?sub_period=post_gfc`` clips the window to 2010–."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        from .fund_composite import DEFAULT_FINANCING_BPS, fund_composite

        fund = _user_fund(request.user)
        if fund is None:
            return Response({"available": False, "reason": "no fund"})
        qp = request.query_params
        try:
            leverage = min(3.0, max(0.5, float(qp.get("leverage", 1.0))))
        except (TypeError, ValueError):
            leverage = 1.0
        try:
            financing_bps = float(qp.get("financing_bps", DEFAULT_FINANCING_BPS))
            financing_bps = min(2000.0, max(0.0, financing_bps))
        except (TypeError, ValueError):
            financing_bps = DEFAULT_FINANCING_BPS
        sub_period = "post_gfc" if qp.get("sub_period") == "post_gfc" else "full"
        return Response(
            fund_composite(
                fund, weights_raw=qp.get("weights"),
                leverage=leverage, financing_bps=financing_bps, sub_period=sub_period,
            )
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
    """Clear the fund halt — the full-restart acknowledgment: rebases the fund
    peak to current aggregate equity, un-halts every member account and rebases
    its peak too, so all drawdown breakers re-arm from today's level (an
    un-rebased resume would be re-halted by the next guardrail sweep, forever)."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request) -> Response:
        fund = _user_fund(request.user)
        if fund is None:
            return Response({"detail": "no fund"}, status=404)
        return Response(fund_layer.resume_fund(fund))
