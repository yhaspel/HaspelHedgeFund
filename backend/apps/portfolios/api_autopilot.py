"""P7 §13 — per-strategy autopilot API (user-scoped, paper-only)."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from rest_framework import permissions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.brokers.models import BrokerAccount, StrategyBrokerLink
from apps.models_catalog.presets import SELECTABLE_PRESETS
from apps.schedules.triggers import describe_cron, is_valid_cron

from .models import AutopilotRun, PortfolioStrategy, StrategyAutopilot
from .validation import validation_status

# Whole-percent / decimal config fields the PUT may set.
_DEC_FIELDS = (
    "cost_ceiling_usd", "target_vol_pct", "dd_soft_cut_pct", "dd_hard_halt_pct",
    "max_notional_per_day_usd", "liquidity_adv_cap_pct",
)
_STR_FIELDS = ("cron_expression", "timezone", "model_preset", "on_breach", "short_mode")
_BOOL_FIELDS = ("is_market_aware", "flatten_on_halt")


def _autopilot_dict(ap: StrategyAutopilot) -> dict:
    return {
        "strategy_id": ap.strategy_id,
        "is_enabled": ap.is_enabled,
        "state": ap.state,
        "cron_expression": ap.cron_expression,
        "cron_description": describe_cron(ap.cron_expression),
        "timezone": ap.timezone,
        "is_market_aware": ap.is_market_aware,
        "broker_account_id": ap.broker_account_id,
        "model_preset": ap.model_preset,
        "cost_ceiling_usd": str(ap.cost_ceiling_usd) if ap.cost_ceiling_usd is not None else None,
        "on_breach": ap.on_breach,
        "target_vol_pct": str(ap.target_vol_pct),
        "dd_soft_cut_pct": str(ap.dd_soft_cut_pct),
        "dd_hard_halt_pct": str(ap.dd_hard_halt_pct),
        "max_orders_per_day": ap.max_orders_per_day,
        "max_notional_per_day_usd": str(ap.max_notional_per_day_usd),
        "liquidity_adv_cap_pct": str(ap.liquidity_adv_cap_pct),
        "short_mode": ap.short_mode,
        "flatten_on_halt": ap.flatten_on_halt,
        "peak_equity_usd": str(ap.peak_equity_usd) if ap.peak_equity_usd is not None else None,
        "last_run_at": ap.last_run_at.isoformat() if ap.last_run_at else None,
        "next_run_at": ap.next_run_at.isoformat() if ap.next_run_at else None,
        "validation": validation_status(ap.strategy),
    }


def _get_strategy(request, pk):
    return PortfolioStrategy.objects.filter(pk=pk, user=request.user).first()


class StrategyAutopilotView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, pk: int) -> Response:
        strategy = _get_strategy(request, pk)
        if strategy is None:
            return Response({"detail": "not found"}, status=404)
        ap = getattr(strategy, "autopilot", None)
        if ap is None:
            return Response({"autopilot": None, "validation": validation_status(strategy)})
        return Response({"autopilot": _autopilot_dict(ap)})

    def put(self, request: Request, pk: int) -> Response:
        strategy = _get_strategy(request, pk)
        if strategy is None:
            return Response({"detail": "not found"}, status=404)
        ap, _ = StrategyAutopilot.objects.get_or_create(strategy=strategy)
        data = request.data or {}

        # Broker account: must belong to the user and be PAPER.
        if "broker_account_id" in data:
            acc = BrokerAccount.objects.filter(
                pk=data["broker_account_id"], user=request.user,
            ).first()
            if acc is None:
                return Response({"detail": "broker account not found"}, status=404)
            if acc.mode != BrokerAccount.MODE_PAPER:
                return Response({"detail": "autopilot is paper-only"}, status=400)
            ap.broker_account = acc

        # Model preset must be a user-selectable preset — a bad value would
        # silently expand to {} and route the council to registry defaults. The
        # offline-only "local" is excluded (SELECTABLE_PRESETS): accepting it
        # would store a preset that hard-errors on a live Ollama probe online.
        if "model_preset" in data and data["model_preset"] not in SELECTABLE_PRESETS:
            choices = ", ".join(sorted(SELECTABLE_PRESETS))
            return Response(
                {"detail": f"invalid model_preset (choose: {choices})"},
                status=400,
            )

        # Cron must be parseable — an invalid expression would clear next_run_at
        # on reschedule (below) and the autopilot would silently never fire.
        if "cron_expression" in data and not is_valid_cron(data["cron_expression"]):
            return Response({"detail": "invalid cron_expression"}, status=400)

        for f in _STR_FIELDS:
            if f in data:
                setattr(ap, f, data[f])
        for f in _BOOL_FIELDS:
            if f in data:
                setattr(ap, f, bool(data[f]))
        for f in _DEC_FIELDS:
            if f in data and data[f] is not None:
                try:
                    setattr(ap, f, Decimal(str(data[f])))
                except (InvalidOperation, ValueError):
                    return Response({"detail": f"invalid {f}"}, status=400)
        if "max_orders_per_day" in data:
            ap.max_orders_per_day = int(data["max_orders_per_day"])

        # is_enabled can only be set true through the validated enable path.
        if data.get("is_enabled") and not ap.is_enabled:
            return self._enable(ap, strategy)
        # A cadence edit on a live autopilot must recompute the next fire time —
        # otherwise next_run_at keeps pointing at the old schedule until the next
        # natural fire (or a resume). Mirrors _enable/Resume, which already do this.
        if ap.is_enabled and any(f in data for f in ("cron_expression", "timezone")):
            ap.reschedule()
        ap.save()
        return Response({"autopilot": _autopilot_dict(ap)})

    @staticmethod
    def _enable(ap, strategy):
        result = validation_status(strategy)
        if not result["passed"]:
            return Response(
                {"detail": "validation gate not passed", "validation": result},
                status=status.HTTP_409_CONFLICT,
            )
        if not StrategyBrokerLink.objects.filter(strategy=strategy, is_active=True).exists():
            return Response({"detail": "no active broker link"}, status=409)
        # §6.0: autopilot REQUIRES auto_run_council — force it on enable.
        if not strategy.auto_run_council:
            PortfolioStrategy.objects.filter(pk=strategy.pk).update(auto_run_council=True)
        ap.is_enabled = True
        if ap.state == StrategyAutopilot.STATE_HALTED:
            ap.state = StrategyAutopilot.STATE_ACTIVE
        ap.reschedule()
        ap.save()
        return Response({"autopilot": _autopilot_dict(ap)})


class StrategyAutopilotEnableView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request, pk: int) -> Response:
        strategy = _get_strategy(request, pk)
        if strategy is None:
            return Response({"detail": "not found"}, status=404)
        ap, _ = StrategyAutopilot.objects.get_or_create(strategy=strategy)
        return StrategyAutopilotView._enable(ap, strategy)


class StrategyAutopilotDisableView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request, pk: int) -> Response:
        strategy = _get_strategy(request, pk)
        if strategy is None:
            return Response({"detail": "not found"}, status=404)
        ap = getattr(strategy, "autopilot", None)
        if ap is None:
            return Response({"detail": "no autopilot"}, status=404)
        ap.is_enabled = False
        ap.next_run_at = None
        ap.save(update_fields=["is_enabled", "next_run_at", "updated_at"])
        return Response({"autopilot": _autopilot_dict(ap)})


class StrategyAutopilotResumeView(APIView):
    """The one human touch-point: clear a drawdown halt. Fail-safe — the next
    drawdown evaluation re-halts if the book hasn't recovered (§6.3)."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request, pk: int) -> Response:
        strategy = _get_strategy(request, pk)
        if strategy is None:
            return Response({"detail": "not found"}, status=404)
        ap = getattr(strategy, "autopilot", None)
        if ap is None:
            return Response({"detail": "no autopilot"}, status=404)
        ap.state = StrategyAutopilot.STATE_ACTIVE
        ap.reschedule()
        ap.save(update_fields=["state", "next_run_at", "updated_at"])
        return Response({"autopilot": _autopilot_dict(ap)})


class StrategyAutopilotRunNowView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request, pk: int) -> Response:
        strategy = _get_strategy(request, pk)
        if strategy is None:
            return Response({"detail": "not found"}, status=404)
        ap = getattr(strategy, "autopilot", None)
        if ap is None or not ap.is_enabled:
            return Response({"detail": "autopilot is not enabled"}, status=409)
        from .tasks_autopilot import trigger_autopilot_now

        run = trigger_autopilot_now(ap)
        return Response({"autopilot_run_id": run.id, "status": "queued"},
                        status=status.HTTP_202_ACCEPTED)


class StrategyAutopilotHistoryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, pk: int) -> Response:
        strategy = _get_strategy(request, pk)
        if strategy is None:
            return Response({"detail": "not found"}, status=404)
        ap = getattr(strategy, "autopilot", None)
        if ap is None:
            return Response({"runs": []})
        runs = AutopilotRun.objects.filter(autopilot=ap).order_by("-fire_time_utc")[:50]
        return Response({"runs": [
            {
                "id": r.id,
                "fire_time": r.fire_time_utc.isoformat(),
                "status": r.status,
                "target_id": r.target_id,
                "n_orders": r.broker_orders.count(),
                "submit_decision": r.submit_decision,
                "guardrail_actions": r.guardrail_actions,
                "error": r.error,
            }
            for r in runs
        ]})


class StrategyExecutedView(APIView):
    """The account's broker book (real fills) + recent autopilot runs."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, pk: int) -> Response:
        strategy = _get_strategy(request, pk)
        if strategy is None:
            return Response({"detail": "not found"}, status=404)
        link = (
            StrategyBrokerLink.objects.filter(strategy=strategy, is_active=True)
            .select_related("broker_account__portfolio").first()
        )
        if link is None:
            return Response({"linked": False, "positions": [], "nav": None})
        pf = link.broker_account.portfolio
        positions = [
            {"ticker": p.ticker, "quantity": str(p.quantity), "avg_cost": str(p.avg_cost)}
            for p in pf.positions.all().order_by("ticker")
        ]
        from .valuation import value_portfolio

        try:
            nav = str(value_portfolio(pf).total_value)
        except Exception:  # noqa: BLE001
            nav = str(pf.cash_balance)
        return Response({
            "linked": True,
            "account_id": link.broker_account_id,
            "account_label": link.broker_account.label,
            "connection_status": link.broker_account.connection_status,
            "cash": str(pf.cash_balance),
            "nav": nav,
            "positions": positions,
        })
