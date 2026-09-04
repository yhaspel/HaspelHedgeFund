"""P7 §13 / P14 — fund-level API: the shared-account rollup, the fund kill
switch, and (P14) everything the Fund tab manages — the fund itself, its shared
paper account, the member roster + allocations, reset and flatten.

All writes go through ``sleeves`` (the single writer); this layer only maps
``FundError`` → HTTP.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from rest_framework import permissions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.brokers.models import BrokerAccount

from . import fund as fund_layer
from . import sleeves
from .models import AutonomousFund, PortfolioStrategy
from .sleeves import FundError
from .validation import validation_status


def _user_fund(user) -> AutonomousFund | None:
    return AutonomousFund.objects.filter(owner=user).order_by("id").first()


def _error(exc: FundError) -> Response:
    return Response({"detail": exc.detail, **exc.extra}, status=exc.status)


class FundView(APIView):
    """GET /api/fund/ — the rollup (``{"fund": null}`` until one exists).

    PUT /api/fund/ — create the user's fund (first call) and/or set its name,
    drawdown halt and the ONE shared paper account every member trades in.
    Body: ``{name?, fund_dd_halt_pct?, broker_account_id?}``."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        fund = _user_fund(request.user)
        if fund is None:
            return Response({"fund": None})
        return Response(fund_layer.fund_overview(fund))

    def put(self, request: Request) -> Response:
        data = request.data or {}
        fund = _user_fund(request.user)
        created = False
        if fund is None:
            fund = AutonomousFund.objects.create(owner=request.user, name="Autonomous Fund")
            created = True
        fields: list[str] = []
        name = (data.get("name") or "").strip()
        if name:
            fund.name = name[:80]
            fields.append("name")
        if data.get("fund_dd_halt_pct") is not None:
            try:
                pct = Decimal(str(data["fund_dd_halt_pct"]))
            except (InvalidOperation, ValueError):
                return Response({"detail": "invalid fund_dd_halt_pct"}, status=400)
            if not (Decimal("0") <= pct <= Decimal("50")):
                return Response({"detail": "fund_dd_halt_pct must be between 0 and 50"}, status=400)
            fund.fund_dd_halt_pct = pct
            fields.append("fund_dd_halt_pct")
        if fields:
            fund.save(update_fields=[*fields, "updated_at"])
        account_res = None
        if "broker_account_id" in data and data["broker_account_id"] is not None:
            acc = BrokerAccount.objects.filter(
                pk=data["broker_account_id"], user=request.user,
            ).select_related("portfolio").first()
            if acc is None:
                return Response({"detail": "broker account not found"}, status=404)
            try:
                account_res = sleeves.configure_account(fund, acc)
            except FundError as exc:
                return _error(exc)
        fund.refresh_from_db()
        return Response(
            {**fund_layer.fund_overview(fund), "created": created, "account": account_res},
            status=status.HTTP_201_CREATED if created else 200,
        )


# Back-compat names (tests / older imports).
FundOverviewView = FundView
FundConfigView = FundView


class FundMembersView(APIView):
    """PUT /api/fund/members/ — replace the roster + allocations.
    Body: ``{members: [{strategy_id, allocation_pct}], force_flatten?: bool}``.
    Active allocations must total 100%. Removing a member that still holds
    positions is refused (409 + ``blocking``) unless ``force_flatten`` queues its
    closing orders."""
    permission_classes = [permissions.IsAuthenticated]

    def put(self, request: Request) -> Response:
        fund = _user_fund(request.user)
        if fund is None:
            return Response({"detail": "no fund — set it up first (PUT /api/fund/)"}, status=404)
        data = request.data or {}
        try:
            summary = sleeves.set_members(
                fund, data.get("members", []), force_flatten=bool(data.get("force_flatten")),
            )
        except FundError as exc:
            return _error(exc)
        fund.refresh_from_db()
        return Response({**fund_layer.fund_overview(fund), "changes": summary})


class FundCandidatesView(APIView):
    """GET /api/fund/candidates/ — the user's strategies as pickable members:
    membership, validation gate, backtest presence. Drives the roster editor."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        from apps.backtests.models import Backtest

        fund = _user_fund(request.user)
        member_pct = {}
        if fund is not None:
            member_pct = {
                sl.strategy_id: str(sl.allocation_pct) for sl in fund.sleeves.filter(is_active=True)
            }
        rows = []
        for s in PortfolioStrategy.objects.filter(user=request.user).order_by("-is_active", "name"):
            rows.append({
                "id": s.id,
                "name": s.name,
                "kind": s.kind,
                "kind_display": s.get_kind_display(),
                "is_active": s.is_active,
                "is_member": s.id in member_pct,
                "allocation_pct": member_pct.get(s.id),
                "validation_passed": bool(validation_status(s).get("passed")),
                "has_backtest": Backtest.objects.filter(strategy=s).exists(),
            })
        return Response({"strategies": rows})


class FundAccountsView(APIView):
    """GET /api/fund/accounts/ — the user's PAPER broker accounts the fund may
    trade (the picker in Fund settings)."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        from apps.brokers.capabilities import get_capabilities

        rows = []
        for acc in BrokerAccount.objects.filter(
            user=request.user, mode=BrokerAccount.MODE_PAPER,
        ).select_related("portfolio").order_by("id"):
            cap = get_capabilities(acc.broker)
            rows.append({
                "id": acc.id,
                "label": acc.label,
                "broker": acc.broker,
                "broker_display": cap.display_name if cap else acc.broker,
                "connection_status": acc.connection_status,
                "is_active": acc.is_active,
                "cash": str(acc.portfolio.cash_balance),
                "positions_count": acc.portfolio.positions.count(),
                "in_fund": acc.funds.exists(),
            })
        return Response({"accounts": rows})


class FundResetView(APIView):
    """POST /api/fund/reset/ — fresh start: split the account's cash between the
    members by allocation %, rebase every breaker, clear the halt. 409 unless
    the account book is flat (positions + in-flight orders)."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request) -> Response:
        fund = _user_fund(request.user)
        if fund is None:
            return Response({"detail": "no fund"}, status=404)
        try:
            result = sleeves.reset_fund(fund)
        except FundError as exc:
            return _error(exc)
        fund.refresh_from_db()
        return Response({**fund_layer.fund_overview(fund), "reset": result})


class FundFlattenView(APIView):
    """POST /api/fund/flatten/ — queue closing orders for every position in the
    shared account (per sleeve where attributed). Market closed ⇒ held for the
    open. The precursor to Reset."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request) -> Response:
        fund = _user_fund(request.user)
        if fund is None:
            return Response({"detail": "no fund"}, status=404)
        try:
            result = sleeves.flatten_fund(fund, reason="manual_flatten")
        except FundError as exc:
            return _error(exc)
        return Response({**fund_layer.fund_overview(fund), "flatten": result})


class FundCompositeView(APIView):
    """P10 §B5 / P11 A3 — GET /api/fund/composite/ — the pods' stitched OOS
    validation curves combined at configurable weights vs SPY-TR/QQQ-TR.

    ``?weights=53:0.6,54:0.2,55:0.2`` (strategy-id:weight, normalized; default
    the live allocation split).
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
    """P10 §C2 — GET /api/fund/history/?days=N — per-member + aggregate NAV
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
    """Fund-level kill switch — halts all members at once (§7/§10)."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request) -> Response:
        fund = _user_fund(request.user)
        if fund is None:
            return Response({"detail": "no fund"}, status=404)
        n = fund_layer.halt_fund(fund, reason="manual_kill_switch")
        return Response({"state": fund.state, "accounts_halted": n})


class FundResumeView(APIView):
    """Clear the fund halt — the full-restart acknowledgment: rebases the fund
    peak to current account equity, un-halts every member and rebases its
    sleeve peak too, so all drawdown breakers re-arm from today's level (an
    un-rebased resume would be re-halted by the next guardrail sweep, forever)."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request) -> Response:
        fund = _user_fund(request.user)
        if fund is None:
            return Response({"detail": "no fund"}, status=404)
        return Response(fund_layer.resume_fund(fund))
