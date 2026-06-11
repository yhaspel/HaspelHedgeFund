"""P7 §9 — demo seed backtest (synthetic; does NOT open the autopilot gate).

Historically this wrote a fabricated ``status=done`` "[seed]" backtest so the §9
enable gate opened out of the box. P10 §B3 ended that: fabricated rows are
``status=synthetic`` (the gate only accepts real ``done`` total-return-era
runs), so a seed can no longer arm live trading. Existing live "[seed]" rows
were re-statused by ``backtests/migrations/0012``.

The seed remains useful as a *cosmetic demo record* (something to render on the
backtests list / fund cards). To actually open the gate, run a real validation
backtest — the deterministic kinds (risk_parity / trend / sector_momentum) cost
$0 and a few minutes. Idempotent — skips a strategy that already has a
qualifying backtest unless ``force`` is set.
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
    """Create a SYNTHETIC demo backtest for ``strategy`` (or return ``None`` if
    it already has a qualifying real one and ``force`` is False).

    P10 §B3: the row is ``status=synthetic`` — clearly badged, deletable, and
    **never** §9-gate evidence. The metric values are still shaped to read
    sensibly in the UI (DD within the hard-halt limit etc.), but only a real
    ``done`` total-return-era backtest can open the enable toggle.
    """
    if not force:
        # Idempotency: skip when the strategy is already truly validated, or
        # when a previous seed exists (a synthetic seed can no longer flip the
        # gate, so the gate check alone would re-seed forever).
        if has_passing_backtest(strategy):
            return None
        if Backtest.objects.filter(strategy=strategy, name=SEED_NAME).exists():
            return None

    hard = _hard_halt_pct(strategy)
    # Keep the demo max drawdown comfortably *within* the hard-halt limit so
    # the record reads plausibly next to the strategy's guardrail config.
    max_dd = (hard - Decimal("1.0")) if hard > Decimal("1.5") else (hard / Decimal("2"))

    today = timezone.now().date()
    bt = Backtest.objects.create(
        user=strategy.user,
        strategy=strategy,
        name=SEED_NAME,
        universe=list(_SEED_UNIVERSE),
        start_date=today - dt.timedelta(days=730),
        end_date=today - dt.timedelta(days=1),
        status=Backtest.SYNTHETIC,
        progress_pct=100,
        progress_message="Seed demo backtest (synthetic — not a real engine run; "
                         "does not open the §9 gate).",
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
