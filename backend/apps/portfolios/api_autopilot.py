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
from .validation import enable_gate, validation_status

# Whole-percent / decimal config fields the PUT may set.
_DEC_FIELDS = (
    "cost_ceiling_usd", "target_vol_pct", "dd_soft_cut_pct", "dd_hard_halt_pct",
    "max_notional_per_day_usd", "liquidity_adv_cap_pct",
)
_STR_FIELDS = ("cron_expression", "timezone", "model_preset", "on_breach", "short_mode")
_BOOL_FIELDS = ("is_market_aware", "flatten_on_halt")

# Inclusive bounds per decimal field: (low, high, low_is_exclusive). These are
# the guardrail knobs the deterministic risk layer reads, so a nonsense value is
# not a cosmetic bug: a 0 / negative dd_hard_halt_pct silently switches the live
# breaker OFF (``hard > 0``) while the §9 gate keeps validating against 7.5%.
_DEC_BOUNDS = {
    # (0.5, 50] — below half a percent the breaker would fire on noise.
    "dd_soft_cut_pct": (Decimal("0.5"), Decimal("50"), True),
    "dd_hard_halt_pct": (Decimal("0.5"), Decimal("50"), True),
    "target_vol_pct": (Decimal("0"), Decimal("200"), True),      # > 0
    "max_notional_per_day_usd": (Decimal("0"), Decimal("1000000000"), False),
    "liquidity_adv_cap_pct": (Decimal("0"), Decimal("100"), False),
    "cost_ceiling_usd": (Decimal("0"), Decimal("100000"), False),
}
# Only this one is nullable on the model, so only it may be cleared with null.
_NULLABLE_DEC_FIELDS = frozenset({"cost_ceiling_usd"})


def _clean_decimal(field: str, raw) -> tuple[Decimal | None, str | None]:
    """Parse + bounds-check one decimal config value. Returns ``(value, error)``;
    a non-finite (NaN / Infinity) or out-of-range value is an error, never a
    stored row."""
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None, f"invalid {field}"
    if not value.is_finite():
        return None, f"{field} must be a finite number"
    low, high, low_exclusive = _DEC_BOUNDS[field]
    too_low = value <= low if low_exclusive else value < low
    if too_low or value > high:
        op = ">" if low_exclusive else "≥"
        return None, f"{field} must be {op} {low} and ≤ {high}"
    return value, None


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
        # P14: the fund sleeve this strategy trades (None = not a fund member).
        "sleeve": _sleeve_dict(ap.strategy),
    }


def _sleeve_dict(strategy) -> dict | None:
    from . import sleeves

    sl = sleeves.sleeve_for(strategy)
    if sl is None:
        return None
    return {
        "fund_id": sl.fund_id,
        "fund_configured": sl.fund.broker_account_id is not None,
        "allocation_pct": str(sl.allocation_pct),
        "initial_capital": str(sl.initial_capital_usd),
        "portfolio_id": sl.portfolio_id,
    }


def _get_strategy(request, pk):
    return PortfolioStrategy.objects.filter(pk=pk, user=request.user).first()


def _rearm_drawdown_breaker(ap: StrategyAutopilot) -> None:
    """Rebase ``peak_equity_usd`` to the book's current equity (or clear it for
    a cold re-seed when the book can't be valued) so the §6.3 drawdown breaker
    re-arms from today's level. Called on every human un-halt path — without
    this, the stale all-time peak re-halts the account on its next evaluation
    no matter how many times the halt is cleared."""
    from apps.portfolios import autopilot_risk

    eq = autopilot_risk.broker_equity(ap)
    ap.peak_equity_usd = eq if (eq is not None and eq > 0) else None


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

        # An unknown IANA zone makes reschedule()/compute_next() raise — a 500
        # here, and (stored on a disabled row) a crash that stalls the whole
        # dispatcher later.
        if "timezone" in data:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            try:
                ZoneInfo(str(data["timezone"]))
            except (ZoneInfoNotFoundError, ValueError, TypeError, ModuleNotFoundError):
                return Response({"detail": "invalid timezone (use an IANA zone)"}, status=400)

        for f, choices in (
            ("short_mode", StrategyAutopilot.SHORT_MODE_CHOICES),
            ("on_breach", StrategyAutopilot.ON_BREACH_CHOICES),
        ):
            if f in data and data[f] not in {c[0] for c in choices}:
                valid = ", ".join(sorted(c[0] for c in choices))
                return Response({"detail": f"invalid {f} (choose: {valid})"}, status=400)

        # Decimals: parse + bounds-check BEFORE anything is assigned, so a bad
        # value can never be half-applied.
        decimals: dict[str, Decimal | None] = {}
        for f in _DEC_FIELDS:
            if f not in data:
                continue
            if data[f] is None or data[f] == "":
                if f in _NULLABLE_DEC_FIELDS:
                    decimals[f] = None
                    continue
                return Response({"detail": f"{f} must not be blank"}, status=400)
            value, err = _clean_decimal(f, data[f])
            if err is not None:
                return Response({"detail": err}, status=400)
            decimals[f] = value
        soft = decimals.get("dd_soft_cut_pct", ap.dd_soft_cut_pct)
        hard = decimals.get("dd_hard_halt_pct", ap.dd_hard_halt_pct)
        if soft is not None and hard is not None and soft >= hard:
            return Response(
                {"detail": "dd_soft_cut_pct must be below dd_hard_halt_pct "
                           f"(got soft {soft}, hard {hard})"},
                status=400,
            )
        if "max_orders_per_day" in data:
            raw = data["max_orders_per_day"]
            if isinstance(raw, bool) or raw is None or raw == "":
                return Response({"detail": "invalid max_orders_per_day"}, status=400)
            try:
                n_orders = int(raw)
            except (TypeError, ValueError):
                return Response({"detail": "invalid max_orders_per_day"}, status=400)
            if n_orders < 0:
                return Response(
                    {"detail": "max_orders_per_day must be an integer ≥ 0 "
                               "(0 means no orders at all)"},
                    status=400,
                )
            ap.max_orders_per_day = n_orders

        for f in _STR_FIELDS:
            if f in data:
                setattr(ap, f, data[f])
        for f in _BOOL_FIELDS:
            if f in data:
                setattr(ap, f, bool(data[f]))
        for f, value in decimals.items():
            setattr(ap, f, value)

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
        # §9 is STRICT for a new enable: the evidence must be of this strategy's
        # universe, on the engine it trades live on, and long enough to mean
        # something. (An already-enabled autopilot is never auto-disabled by the
        # same checks — they surface as validation_warnings instead.)
        result = enable_gate(strategy)
        if not result["passed"]:
            return Response(
                {
                    "detail": "validation gate not passed",
                    "reasons": result["reasons"],
                    "validation": result,
                },
                status=status.HTTP_409_CONFLICT,
            )
        if not StrategyBrokerLink.objects.filter(strategy=strategy, is_active=True).exists():
            from . import sleeves

            if sleeves.sleeve_for(strategy) is not None:
                return Response(
                    {"detail": "the fund has no paper account yet — choose one in Fund settings"},
                    status=409,
                )
            return Response({"detail": "no active broker link"}, status=409)
        # §6.0: autopilot REQUIRES auto_run_council — force it on enable.
        if not strategy.auto_run_council:
            PortfolioStrategy.objects.filter(pk=strategy.pk).update(auto_run_council=True)
        ap.is_enabled = True
        if ap.state == StrategyAutopilot.STATE_HALTED:
            # Re-enabling out of a halt is the same human acknowledgment as
            # Resume — re-arm the breaker, else the stale peak re-halts the
            # account on its first pre-flight.
            _rearm_drawdown_breaker(ap)
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
    """The one human touch-point: clear a drawdown halt. Resuming acknowledges
    the loss — the peak rebases to the book's current equity so the §6.3
    breaker re-arms from today's level (a halted book can't trade its way back
    above a stale peak, so an un-rebased resume would re-halt on the very next
    evaluation, forever). Fail-safe is preserved: a fresh drawdown of the
    configured size from the acknowledged level halts again."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request, pk: int) -> Response:
        strategy = _get_strategy(request, pk)
        if strategy is None:
            return Response({"detail": "not found"}, status=404)
        ap = getattr(strategy, "autopilot", None)
        if ap is None:
            return Response({"detail": "no autopilot"}, status=404)
        from . import sleeves

        if sleeves.fund_halted(strategy):
            # A fund halt is firm-wide: clearing it one member at a time would
            # leave the Fund page saying "halted" while the pod traded again.
            return Response(
                {"detail": "the fund is halted — resume the fund "
                           "(POST /api/fund/resume/) to re-arm its members."},
                status=status.HTTP_409_CONFLICT,
            )
        _rearm_drawdown_breaker(ap)
        ap.state = StrategyAutopilot.STATE_ACTIVE
        ap.reschedule()
        ap.save(update_fields=["state", "peak_equity_usd", "next_run_at", "updated_at"])
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
    """The strategy's executed book (real fills): its fund SLEEVE on the shared
    account (P14) or, for a legacy stand-alone link, the whole account book."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, pk: int) -> Response:
        from . import sleeves

        strategy = _get_strategy(request, pk)
        if strategy is None:
            return Response({"detail": "not found"}, status=404)
        sleeve = sleeves.sleeve_for(strategy)
        link = (
            StrategyBrokerLink.objects.filter(strategy=strategy, is_active=True)
            .select_related("broker_account__portfolio").first()
        )
        if sleeve is None and link is None:
            return Response({"linked": False, "positions": [], "nav": None})
        account = (
            sleeve.fund.broker_account if (sleeve is not None and sleeve.fund.broker_account_id)
            else (link.broker_account if link is not None else None)
        )
        pf = sleeve.portfolio if sleeve is not None else link.broker_account.portfolio
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
            "linked": account is not None,
            "is_sleeve": sleeve is not None,
            "account_id": account.id if account is not None else None,
            "account_label": account.label if account is not None else None,
            "connection_status": account.connection_status if account is not None else None,
            "cash": str(pf.cash_balance),
            "nav": nav,
            "positions": positions,
        })
