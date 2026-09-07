"""P7 §7 / P14 — the fund layer over ONE shared paper account.

Per-sleeve §6 is where risk is *enforced*; this layer is reporting + a global
stop:

  * ``fund_overview`` — the shared account (NAV / cash / connection), one card
    per member sleeve (allocation, capital, NAV, P&L, state, schedule, set-up
    hints), aggregate NAV/drawdown off the ACCOUNT book, the unallocated
    residual (Σ sleeves vs account), reset readiness, the realized N×N
    return-correlation matrix (suppressed below a minimum sample) and rolling-
    Sharpe *recommendations*.
  * ``evaluate_fund_drawdown`` — account-equity drawdown past
    ``fund_dd_halt_pct`` halts every member (the firm-level cap).
  * ``halt_fund`` / ``resume_fund`` — the manual fund kill switch. Resume is
    the full-restart acknowledgment: it rebases the fund + sleeve peaks to
    current equity so the breakers re-arm from today's level (an un-rebased
    resume would be re-halted by the next sweep, forever).

Before the shared account is chosen (``fund.broker_account`` is None) the fund
is "not configured": members are listed with their (still empty) sleeves and
nothing trades — Reset funds the sleeves once the account is in place.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.utils import timezone

from apps.schedules.triggers import describe_cron

from . import sleeves
from .autopilot_audit import last_caps_shadow
from .models import AutonomousFund, AutopilotRun, StrategyAutopilot
from .validation import validation_status

log = logging.getLogger(__name__)

MIN_CORRELATION_SAMPLE = 8  # weekly returns; below this the N×N matrix is noise.


def _account_setup(strategy, ap, *, fund_configured: bool) -> dict:
    """Why a member is (not) live + the single next step, so the fund cards can
    be honest instead of showing a schedule that will never fire.

    Returns ``{validation_passed, can_enable, setup_hint}``. ``setup_hint`` is a
    short, action-oriented line (``None`` once enabled). ``can_enable`` is True
    only when the §9 gate passes AND the fund has its shared account AND it
    isn't already on — i.e. one click on the member's panel away from trading.
    """
    from apps.brokers.models import StrategyBrokerLink

    if ap is not None and ap.is_enabled:
        return {"validation_passed": True, "can_enable": False, "setup_hint": None}

    passed = bool(validation_status(strategy).get("passed"))
    has_link = StrategyBrokerLink.objects.filter(
        strategy=strategy, is_active=True
    ).exists()

    if not fund_configured and not has_link:
        hint = "Choose the fund's paper account (Fund settings) to make members enable-able."
    elif ap is None:
        hint = "Open the strategy's panel to set up its autopilot."
    elif not passed:
        hint = "Run a validation backtest to unlock the enable toggle."
    elif not has_link:
        hint = "Choose the fund's paper account to enable."
    else:
        hint = "Validated — open the strategy's panel and enable."
    return {
        "validation_passed": passed,
        "can_enable": ap is not None and passed and has_link,
        "setup_hint": hint,
    }


def account_equity(strategy) -> Decimal | None:
    """Current marked NAV of the strategy's book of record: its fund sleeve (P14)
    or, for a legacy stand-alone link, its whole broker account."""
    return sleeves.book_value(sleeves.member_book(strategy))


def _series_start(strategy):
    """When this strategy's current capital base began: a fund sleeve's latest
    external cash flow (Reset, join, account change). Cycles fired before it ran
    on a different base — the retired per-strategy account, or a previous split
    — so chaining their equity into today's would print the funding step as a
    return (a $100k account → $50k sleeve reset read as −50% in one week, i.e. a
    deeply negative rolling Sharpe and a spurious 1.00 correlation between pods
    that merely shared the same Reset). None = no sleeve / never funded."""
    sleeve = sleeves.sleeve_for(strategy)
    if sleeve is None:
        return None
    from .models import LedgerEntry

    last_flow = (
        LedgerEntry.objects.filter(
            portfolio_id=sleeve.portfolio_id,
            kind__in=(LedgerEntry.KIND_DEPOSIT, LedgerEntry.KIND_WITHDRAWAL),
        )
        .order_by("-created_at")
        .first()
    )
    return last_flow.created_at if last_flow is not None else None


def _equity_series(strategy) -> list[float]:
    """Equity snapshots over time for a strategy, from its AutopilotRun history
    (each cycle records the drawdown eval's equity in guardrail_actions). Used
    only for the correlation/rolling-Sharpe reporting. For a fund member the
    series starts at the sleeve's last funding (see ``_series_start``)."""
    ap = getattr(strategy, "autopilot", None)
    if ap is None:
        return []
    runs = AutopilotRun.objects.filter(autopilot=ap)
    start = _series_start(strategy)
    if start is not None:
        runs = runs.filter(fire_time_utc__gte=start)
    series: list[float] = []
    for run in runs.order_by("fire_time_utc"):
        eq = (run.guardrail_actions or {}).get("drawdown", {}).get("equity")
        if eq is not None:
            try:
                series.append(float(eq))
            except (TypeError, ValueError):
                continue
    return series


def _returns(series: list[float]) -> list[float]:
    return [
        series[i] / series[i - 1] - 1.0
        for i in range(1, len(series))
        if series[i - 1] > 0
    ]


def _pearson(a: list[float], b: list[float]) -> float | None:
    n = min(len(a), len(b))
    if n < 2:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return None
    return cov / (va ** 0.5 * vb ** 0.5)


def _sharpe(returns: list[float]) -> float | None:
    n = len(returns)
    if n < 2:
        return None
    m = sum(returns) / n
    var = sum((r - m) ** 2 for r in returns) / (n - 1)
    sd = var ** 0.5
    if sd <= 0:
        return None
    return (m / sd) * (52 ** 0.5)  # weekly → annualised


def correlation_matrix(strategies) -> dict:
    """Realized pairwise return correlation across the member strategies, or an
    'insufficient_data' marker until each pair has ≥ MIN_CORRELATION_SAMPLE
    weekly returns (cold-start guard)."""
    rets = {s.id: _returns(_equity_series(s)) for s in strategies}
    labels = {s.id: s.name for s in strategies}
    if any(len(r) < MIN_CORRELATION_SAMPLE for r in rets.values()):
        return {
            "available": False,
            "reason": "insufficient_data",
            "min_sample": MIN_CORRELATION_SAMPLE,
            "have": {labels[sid]: len(r) for sid, r in rets.items()},
        }
    ids = list(rets.keys())
    matrix = {
        labels[i]: {labels[j]: round(_pearson(rets[i], rets[j]) or 0.0, 3) for j in ids}
        for i in ids
    }
    return {"available": True, "matrix": matrix}


def _account_dict(fund: AutonomousFund) -> dict | None:
    """The shared account as the Fund tab shows it (None while unconfigured)."""
    acc = fund.broker_account
    if acc is None:
        return None
    from apps.brokers.capabilities import get_capabilities

    cap = get_capabilities(acc.broker)
    pf = acc.portfolio
    nav = sleeves.book_value(pf)
    return {
        "id": acc.id,
        "label": acc.label,
        "broker": acc.broker,
        "broker_display": cap.display_name if cap else acc.broker,
        "mode": acc.mode,
        "connection_status": acc.connection_status,
        "last_synced_at": acc.last_synced_at.isoformat() if acc.last_synced_at else None,
        "cash": str(pf.cash_balance),
        "nav": str(nav) if nav is not None else None,
        "positions_count": pf.positions.count(),
        "portfolio_id": pf.id,
    }


def fund_equity(fund: AutonomousFund) -> Decimal:
    """Fund NAV: the shared account's marked book (the truth). Falls back to the
    sum of the members' books while the fund is unconfigured (legacy layout)."""
    book = sleeves.account_book(fund)
    if book is not None:
        return sleeves.book_value(book) or Decimal("0")
    total = Decimal("0")
    for s in fund.strategies.all():
        eq = account_equity(s)
        if eq is not None:
            total += eq
    return total


def fund_overview(fund: AutonomousFund) -> dict:
    """The rollup for ``/api/fund/``."""
    # Local import — portfolios → backtests would otherwise be a cycle (mirrors
    # validation.py). phase-09a §6.3: drives the card's Run/Re-run verb.
    from apps.backtests.models import Backtest

    configured = fund.is_configured
    active_sleeves = list(fund.active_sleeves())
    strategies = [sl.strategy for sl in active_sleeves]
    members = []
    sleeve_nav_total = Decimal("0")
    for sl in active_sleeves:
        s = sl.strategy
        ap = getattr(s, "autopilot", None)
        eq = account_equity(s)
        if eq is not None:
            sleeve_nav_total += eq
        series = _equity_series(s)
        rets = _returns(series)
        initial = Decimal(sl.initial_capital_usd or 0)
        pnl_pct = (
            round(float((eq - initial) / initial * 100), 3)
            if (eq is not None and initial > 0) else None
        )
        members.append({
            "strategy_id": s.id,
            "name": s.name,
            "kind": s.kind,
            "state": ap.state if ap else None,
            "is_enabled": ap.is_enabled if ap else False,
            "nav": str(eq) if eq is not None else None,
            "peak_equity": str(ap.peak_equity_usd) if (ap and ap.peak_equity_usd) else None,
            "rolling_sharpe": _sharpe(rets[-13:]) if len(rets) >= 2 else None,
            "next_run_at": ap.next_run_at.isoformat() if (ap and ap.next_run_at) else None,
            # next_run_at stays UTC; this is the zone the cron is interpreted in,
            # so the UI can render "Fri 16:30 America/New_York" honestly.
            "timezone": ap.timezone if ap else None,
            "cron_description": describe_cron(ap.cron_expression) if ap else None,
            # Shadow-mode daily caps from the member's last run (never blocking).
            "caps_shadow": last_caps_shadow(ap) if ap is not None else None,
            # §9 evidence-fit checks. Blocking for a NEW enable (enable_gate);
            # advisory here — a running pod is never auto-disabled by them.
            "validation_warnings": validation_status(s).get("warnings", []),
            # Run vs Re-run verb. Any linked backtest (any status) counts — distinct
            # from validation_passed, which gates the §9 enable toggle.
            "has_backtest": Backtest.objects.filter(strategy=s).exists(),
            # P14 sleeve: the slice of the shared pool this strategy trades.
            "allocation_pct": str(sl.allocation_pct),
            "initial_capital": str(initial),
            "cash": str(sl.portfolio.cash_balance),
            "positions_count": sl.portfolio.positions.count(),
            "pnl_pct": pnl_pct,
            "sleeve_portfolio_id": sl.portfolio_id,
            # Why it's not live + the one next step (drives the card CTA/reason).
            **_account_setup(s, ap, fund_configured=configured),
        })
    # Members that were removed while still holding positions — their closes
    # keep attributing here until flat; surfaced so it's never a mystery why the
    # account still holds a name no card claims.
    leaving = []
    for sl in fund.sleeves.filter(is_active=False).select_related("strategy", "portfolio"):
        n = sl.portfolio.positions.count()
        if n:
            leaving.append({
                "strategy_id": sl.strategy_id, "name": sl.strategy.name, "positions_count": n,
            })

    # Recommendation (NOT auto-reallocation — slices float by design).
    recs = []
    sharpes = [
        (m["name"], m["rolling_sharpe"])
        for m in members if m["rolling_sharpe"] is not None
    ]
    if len(sharpes) >= 2:
        worst = min(sharpes, key=lambda x: x[1])
        if worst[1] is not None and worst[1] < 0:
            recs.append(
                f"{worst[0]} has a negative rolling Sharpe — "
                "consider reducing its allocation at the next reset."
            )
    agg_nav = fund_equity(fund)
    # Current drawdown-from-peak (what the halt breaker sees) — surfaced so the
    # dashboard can show WHY the fund is halted / how close it is to the limit.
    peak = fund.peak_equity_usd
    drawdown_pct = None
    if peak and peak > 0 and agg_nav > 0:
        drawdown_pct = round(max(0.0, float((peak - agg_nav) / peak * 100)), 3)
    unallocated = (agg_nav - sleeve_nav_total).quantize(Decimal("0.01")) if configured else None
    allocation_total = sum((Decimal(sl.allocation_pct) for sl in active_sleeves), Decimal("0"))
    return {
        "fund_id": fund.id,
        "name": fund.name,
        "state": fund.state,
        "is_configured": configured,
        "broker_account": _account_dict(fund),
        # "active" only means "not halted" — a fund can be active with every
        # member disabled (nothing trades). is_live is the "actually running"
        # signal: configured AND not halted AND ≥1 member enabled.
        "is_live": configured
        and fund.state == AutonomousFund.STATE_ACTIVE
        and any(m["is_enabled"] for m in members),
        "aggregate_nav": str(agg_nav),
        "peak_equity": str(fund.peak_equity_usd) if fund.peak_equity_usd else None,
        "drawdown_pct": drawdown_pct,
        "fund_dd_halt_pct": str(fund.fund_dd_halt_pct),
        "members": members,
        "members_count": len(members),
        "allocation_total_pct": str(allocation_total),
        "unallocated_nav": str(unallocated) if unallocated is not None else None,
        "attribution_gap": sleeves.attribution_gap(fund) if configured else None,
        "leaving": leaving,
        "reset": sleeves.reset_readiness(fund),
        "inflight_orders": sleeves.inflight_orders(fund).count() if configured else 0,
        "correlation": correlation_matrix(strategies),
        "recommendations": recs,
    }


def evaluate_fund_drawdown(fund: AutonomousFund) -> dict:
    """Aggregate-equity drawdown breaker: past ``fund_dd_halt_pct`` halts every
    member (the firm-level cap over the per-sleeve caps). Cold-start seeds the
    fund peak. Equity is the shared ACCOUNT's marked book."""
    equity = fund_equity(fund)
    if equity <= 0:
        return {"skipped": "no equity"}
    peak = fund.peak_equity_usd
    if peak is None or peak <= 0:
        fund.peak_equity_usd = equity
        fund.save(update_fields=["peak_equity_usd", "updated_at"])
        return {"seeded_peak": str(equity), "drawdown_pct": 0.0}
    if equity > peak:
        peak = equity
    dd_pct = float((peak - equity) / peak * 100) if peak > 0 else 0.0
    fund.peak_equity_usd = peak
    limit = float(fund.fund_dd_halt_pct or 0)
    halted = False
    if limit > 0 and dd_pct >= limit and fund.state != AutonomousFund.STATE_HALTED:
        halt_fund(fund, reason="aggregate_drawdown")
        halted = True
    fund.save(update_fields=["peak_equity_usd", "updated_at"])
    return {"equity": str(equity), "drawdown_pct": round(dd_pct, 3), "halted": halted}


def halt_fund(fund: AutonomousFund, *, reason: str = "manual") -> int:
    """Fund kill switch: halt every member at once. Returns the count."""
    fund.state = AutonomousFund.STATE_HALTED
    fund.save(update_fields=["state", "updated_at"])
    n = StrategyAutopilot.objects.filter(
        strategy__in=fund.strategies.all(),
    ).exclude(state=StrategyAutopilot.STATE_HALTED).update(
        state=StrategyAutopilot.STATE_HALTED, updated_at=timezone.now(),
    )
    log.warning("fund halted fund=%s reason=%s members=%s", fund.id, reason, n)
    # Wave 3: the kill switch had NO persisted record — only this log line — so
    # the activity feed could never show the single most important fund event.
    from .fund_activity import record_fund_event
    from .models import FundEvent

    record_fund_event(
        fund, FundEvent.KIND_FUND_HALT, f"Fund halted ({reason})",
        severity=FundEvent.SEVERITY_ERROR,
        detail=(
            f"{n} member(s) moved to halted. Nothing trades until "
            "POST /api/fund/resume/ acknowledges it."
        ),
        payload={"reason": reason, "members_halted": n},
    )
    return n


def resume_fund(fund: AutonomousFund) -> dict:
    """Clear the fund halt — the full-restart acknowledgment.

    A drawdown halt can never clear itself: a halted fund doesn't trade, so
    equity stays pinned below the stored peak and an un-rebased resume would be
    re-halted by the next guardrail sweep, forever. Resuming therefore means
    "I acknowledge the loss — restart from here": the fund peak is rebased to
    current account equity AND every member is un-halted with its own peak
    rebased to its sleeve's current equity, so each drawdown breaker re-arms at
    the configured distance below TODAY's level. Fail-safety is preserved — a
    fresh breach from the acknowledged level halts again. A peak that can't be
    valued right now is cleared instead (None), which re-seeds at the next
    evaluation (same effect: drawdown restarts at 0).
    """
    from . import autopilot_risk

    strategies = list(fund.strategies.select_related("autopilot").all())
    equity = fund_equity(fund)
    fund.state = AutonomousFund.STATE_ACTIVE
    fund.peak_equity_usd = equity if equity > 0 else None
    fund.save(update_fields=["state", "peak_equity_usd", "updated_at"])

    accounts_resumed = 0
    for s in strategies:
        ap = getattr(s, "autopilot", None)
        if ap is None:
            continue
        eq = autopilot_risk.broker_equity(ap)
        ap.peak_equity_usd = eq if (eq is not None and eq > 0) else None
        if ap.state != StrategyAutopilot.STATE_ACTIVE:
            ap.state = StrategyAutopilot.STATE_ACTIVE
            accounts_resumed += 1
        # A halted member skipped its fires; recompute the next one (no-op /
        # clears when disabled — reschedule() guards internally).
        ap.reschedule()
        ap.save(update_fields=["state", "peak_equity_usd", "next_run_at", "updated_at"])
    log.warning(
        "fund resumed fund=%s peak_rebased_to=%s members_resumed=%s",
        fund.id, fund.peak_equity_usd, accounts_resumed,
    )
    peak = str(fund.peak_equity_usd) if fund.peak_equity_usd is not None else None
    from .fund_activity import record_fund_event
    from .models import FundEvent

    record_fund_event(
        fund, FundEvent.KIND_FUND_RESUME, "Fund resumed",
        severity=FundEvent.SEVERITY_WARN,
        detail=(
            f"{accounts_resumed} member(s) un-halted; drawdown peaks rebased to "
            f"{peak or 'unvalued (re-seeds at the next evaluation)'}."
        ),
        payload={"accounts_resumed": accounts_resumed, "peak_rebased_to": peak},
    )
    return {
        "state": fund.state,
        "accounts_resumed": accounts_resumed,
        "peak_rebased_to": peak,
    }
