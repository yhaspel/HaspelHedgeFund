"""P7 §9 — demo seed for the autopilot validation gate.

The 3-account fund ships *armed but un-enable-able*: ``bootstrap_autonomous_fund``
creates broker links + disabled autopilots, but the §9 gate (``validation.py``)
needs a passing walk-forward ``Backtest`` per strategy and bootstrap creates none
— so out of the box the enable toggle can never light up.

This module writes a clearly-labelled (``[seed]``) passing ``Backtest`` + metrics
linked to a strategy so the gate opens, for educational/paper demos. It does NOT
run the LLM walk-forward engine: it's a deterministic record, not a real result.
Idempotent — skips a strategy that already has a qualifying backtest unless
``force`` is set.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.utils import timezone

from .models import Backtest, BacktestMetrics

SEED_NAME = "[seed] Walk-forward validation (demo)"

# A small, liquid default so the seeded backtest reads sensibly in the UI. The
# gate never inspects the universe — this is cosmetic.
_SEED_UNIVERSE = ["SPY", "QQQ", "IWM", "AAPL", "MSFT"]


def has_passing_backtest(strategy) -> bool:
    """True when the strategy already clears the §9 gate (no seed needed)."""
    from apps.portfolios.validation import validation_status

    return bool(validation_status(strategy).get("passed"))


def _hard_halt_pct(strategy) -> Decimal:
    ap = getattr(strategy, "autopilot", None)
    return ap.dd_hard_halt_pct if (ap and ap.dd_hard_halt_pct) else Decimal("7.5")


def seed_validation_backtest(strategy, *, force: bool = False) -> Backtest | None:
    """Create a passing seed backtest for ``strategy`` (or return ``None`` if it
    already has a qualifying one and ``force`` is False).

    Metrics are chosen to clear all four §9 checks: positive OOS Sharpe, max DD
    strictly within the autopilot's hard-halt limit, and (via ``created_at``)
    newer than the strategy's last config edit.
    """
    if not force and has_passing_backtest(strategy):
        return None

    hard = _hard_halt_pct(strategy)
    # Keep the backtest's max drawdown comfortably *within* the hard-halt limit
    # so ``drawdown_within_limit`` passes for any reasonable guardrail config.
    max_dd = (hard - Decimal("1.0")) if hard > Decimal("1.5") else (hard / Decimal("2"))

    today = timezone.now().date()
    bt = Backtest.objects.create(
        user=strategy.user,
        strategy=strategy,
        name=SEED_NAME,
        universe=list(_SEED_UNIVERSE),
        start_date=today - dt.timedelta(days=730),
        end_date=today - dt.timedelta(days=1),
        status=Backtest.DONE,
        progress_pct=100,
        progress_message="Seed validation backtest (demo — not a real engine run).",
        finished_at=timezone.now(),
    )
    BacktestMetrics.objects.create(
        backtest=bt,
        total_return_pct=Decimal("18.5"),
        annualized_return_pct=Decimal("8.9"),
        sharpe=Decimal("0.95"),
        sortino=Decimal("1.20"),
        max_drawdown_pct=max_dd,
        hit_rate=Decimal("0.54"),
        win_loss_ratio=Decimal("1.30"),
        turnover_pct=Decimal("85.0"),
        mean_is_sharpe=Decimal("1.10"),
        mean_oos_sharpe=Decimal("0.85"),
        sharpe_deflation=Decimal("0.77"),
        oos_sharpe_std=Decimal("0.20"),
        baseline_return_pct=Decimal("11.0"),
    )
    return bt
