"""P7 §7 — fund-level layer over the 3 isolated paper accounts.

Per-account §6 is where risk is *enforced*; this layer is reporting + a global
stop (NOT auto-reallocation — isolated paper accounts can't share cash):

  * ``fund_overview`` — per-account NAV/return/state cards + aggregate
    NAV/return/drawdown + the realized 3×3 return-correlation matrix (suppressed
    below a minimum sample) + rolling-Sharpe *recommendations*.
  * ``evaluate_fund_drawdown`` — aggregate-equity drawdown past
    ``fund_dd_halt_pct`` halts all member accounts (the firm-level cap).
  * ``halt_fund`` / ``resume_fund`` — the manual fund kill switch.

The realized correlation is "measure, don't assume": Accounts 1 & 2 are both
equity, so expect their pairwise number to run high — that is information.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.utils import timezone

from apps.schedules.triggers import describe_cron

from .models import AutonomousFund, AutopilotRun, StrategyAutopilot
from .validation import validation_status

log = logging.getLogger(__name__)

MIN_CORRELATION_SAMPLE = 8  # weekly returns; below this the 3×3 matrix is noise.


def _account_setup(strategy, ap) -> dict:
    """Why an account is (not) live + the single next step, so the fund cards
    can be honest instead of showing a schedule that will never fire.

    Returns ``{validation_passed, can_enable, setup_hint}``. ``setup_hint`` is a
    short, action-oriented line (``None`` once enabled). ``can_enable`` is True
    only when the §9 gate passes AND a paper broker link exists AND it isn't
    already on — i.e. one click on the Autopilot page away from trading.
    """
    from apps.brokers.models import StrategyBrokerLink

    if ap is not None and ap.is_enabled:
        return {"validation_passed": True, "can_enable": False, "setup_hint": None}

    passed = bool(validation_status(strategy).get("passed"))
    has_link = StrategyBrokerLink.objects.filter(
        strategy=strategy, is_active=True
    ).exists()

    if ap is None:
        hint = "Open Autopilot to set this account up."
    elif not passed:
        hint = "Run a validation backtest to unlock the enable toggle."
    elif not has_link:
        hint = "Connect a paper broker account to enable."
    else:
        hint = "Validated — open Autopilot and enable."
    return {
        "validation_passed": passed,
        "can_enable": ap is not None and passed and has_link,
        "setup_hint": hint,
    }


def account_equity(strategy) -> Decimal | None:
    """Current marked NAV of the strategy's linked broker book (real fills)."""
    from apps.brokers.models import StrategyBrokerLink

    from .valuation import value_portfolio

    link = (
        StrategyBrokerLink.objects.filter(strategy=strategy, is_active=True)
        .select_related("broker_account__portfolio")
        .first()
    )
    if link is None or link.broker_account.portfolio is None:
        return None
    try:
        return Decimal(str(value_portfolio(link.broker_account.portfolio).total_value))
    except Exception:  # noqa: BLE001
        return None


def _equity_series(strategy) -> list[float]:
    """Equity snapshots over time for a strategy, from its AutopilotRun history
    (each cycle records the drawdown eval's equity in guardrail_actions). Used
    only for the correlation/rolling-Sharpe reporting."""
    ap = getattr(strategy, "autopilot", None)
    if ap is None:
        return []
    series: list[float] = []
    for run in AutopilotRun.objects.filter(autopilot=ap).order_by("fire_time_utc"):
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


def fund_overview(fund: AutonomousFund) -> dict:
    """The 3-account rollup for ``/api/fund/``."""
    # Local import — portfolios → backtests would otherwise be a cycle (mirrors
    # validation.py). phase-09a §6.3: drives the card's Run/Re-run verb.
    from apps.backtests.models import Backtest

    strategies = list(fund.strategies.select_related("autopilot").all())
    per_account = []
    agg_nav = Decimal("0")
    for s in strategies:
        ap = getattr(s, "autopilot", None)
        eq = account_equity(s)
        if eq is not None:
            agg_nav += eq
        series = _equity_series(s)
        rets = _returns(series)
        per_account.append({
            "strategy_id": s.id,
            "name": s.name,
            "kind": s.kind,
            "state": ap.state if ap else None,
            "is_enabled": ap.is_enabled if ap else False,
            "nav": str(eq) if eq is not None else None,
            "peak_equity": str(ap.peak_equity_usd) if (ap and ap.peak_equity_usd) else None,
            "rolling_sharpe": _sharpe(rets[-13:]) if len(rets) >= 2 else None,
            "next_run_at": ap.next_run_at.isoformat() if (ap and ap.next_run_at) else None,
            "cron_description": describe_cron(ap.cron_expression) if ap else None,
            # Run vs Re-run verb. Any linked backtest (any status) counts — distinct
            # from validation_passed, which gates the §9 enable toggle.
            "has_backtest": Backtest.objects.filter(strategy=s).exists(),
            # Why it's not live + the one next step (drives the card CTA/reason).
            **_account_setup(s, ap),
        })
    # Recommendation (NOT auto-reallocation — paper accounts can't share cash).
    recs = []
    sharpes = [
        (p["name"], p["rolling_sharpe"])
        for p in per_account if p["rolling_sharpe"] is not None
    ]
    if len(sharpes) >= 2:
        worst = min(sharpes, key=lambda x: x[1])
        if worst[1] is not None and worst[1] < 0:
            recs.append(
                f"{worst[0]} has a negative rolling Sharpe — "
                "consider reducing its mandate."
            )
    return {
        "fund_id": fund.id,
        "name": fund.name,
        "state": fund.state,
        # "active" only means "not halted" — a fund can be active with every
        # account disabled (nothing trades). is_live is the "actually running"
        # signal: not halted AND ≥1 account enabled. (Drives the dashboard banner.)
        "is_live": fund.state == AutonomousFund.STATE_ACTIVE
        and any(p["is_enabled"] for p in per_account),
        "aggregate_nav": str(agg_nav),
        "peak_equity": str(fund.peak_equity_usd) if fund.peak_equity_usd else None,
        "fund_dd_halt_pct": str(fund.fund_dd_halt_pct),
        "per_account": per_account,
        "correlation": correlation_matrix(strategies),
        "recommendations": recs,
    }


def evaluate_fund_drawdown(fund: AutonomousFund) -> dict:
    """Aggregate-equity drawdown breaker: past ``fund_dd_halt_pct`` halts every
    member account (the firm-level cap over the per-pod caps). Cold-start seeds
    the fund peak."""
    strategies = list(fund.strategies.all())
    equity = Decimal("0")
    for s in strategies:
        eq = account_equity(s)
        if eq is not None:
            equity += eq
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
    """Fund kill switch: halt every member account at once. Returns the count."""
    fund.state = AutonomousFund.STATE_HALTED
    fund.save(update_fields=["state", "updated_at"])
    n = StrategyAutopilot.objects.filter(
        strategy__in=fund.strategies.all(),
    ).exclude(state=StrategyAutopilot.STATE_HALTED).update(
        state=StrategyAutopilot.STATE_HALTED, updated_at=timezone.now(),
    )
    log.warning("fund halted fund=%s reason=%s accounts=%s", fund.id, reason, n)
    return n


def resume_fund(fund: AutonomousFund) -> None:
    """Clear the fund halt. Per-account halts are NOT auto-cleared — each account
    re-evaluates its own drawdown on the next tick (fail-safe)."""
    fund.state = AutonomousFund.STATE_ACTIVE
    fund.save(update_fields=["state", "updated_at"])
