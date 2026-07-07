"""P7 §9 — autopilot validation gate.

Autopilot cannot be enabled on an unvalidated strategy. The research's
make-or-break warning: an LLM+screener is a multiple-testing machine, so
out-of-sample Sharpe is the only honest acceptance criterion. v1 gates on the
existing walk-forward metrics (a true trial-count-adjusted Deflated Sharpe is a
deferred follow-on — §18). P10 §B3 hardened the evidence rules:

  * a ``status=DONE`` ``Backtest`` linked to the strategy exists — synthetic
    (``[seed]``) demo rows are status=``synthetic`` and never qualify, and
    pre-PR#50 price-only-era rows (``data_era=price_only``) are excluded
    because that data is known to understate returns/Sharpe,
  * ``BacktestMetrics.mean_oos_sharpe > 0`` (positive mean-of-folds OOS Sharpe),
  * ``BacktestMetrics.sharpe > 0`` — the **stitched** long-run OOS Sharpe; the
    mean-of-folds number runs ~0.3–0.4 higher (averaging noisy short-fold
    Sharpes biases upward), so both must clear zero,
  * ``max_drawdown_pct`` within the autopilot's hard-halt limit,
  * the backtest is **newer than the strategy's last config edit**
    (``Backtest.created_at > PortfolioStrategy.updated_at``) — editing caps /
    weights re-locks the toggle until a fresh passing backtest exists.

A one-time setup gate (not a step in the trading loop), so it does not conflict
with "no human intervention" during operation.
"""
from __future__ import annotations

from decimal import Decimal


def _hard_halt_pct(strategy) -> Decimal:
    ap = getattr(strategy, "autopilot", None)
    return ap.dd_hard_halt_pct if (ap and ap.dd_hard_halt_pct) else Decimal("7.5")


def validation_status(strategy) -> dict:
    """Return the §9 checklist for the strategy's autopilot enable toggle:
    ``{passed, checks: [{key, ok, detail}], backtest_id}``."""
    from apps.backtests.models import Backtest, BacktestMetrics

    from .models import PortfolioStrategy

    checks: list[dict] = []

    # P11 F — scaffolding kinds backtest for research but are NOT live-deployable
    # (e.g. single-name L/S needs survivorship-clean data + margin infra first).
    # Hard-block arming regardless of backtest metrics.
    if strategy.kind in PortfolioStrategy.SCAFFOLDING_KINDS:
        checks.append({
            "key": "deployable_kind", "ok": False,
            "detail": "This strategy kind is research scaffolding and cannot be "
                      "armed yet — it needs a survivorship-clean single-name "
                      "backfill (F1) and margin support before live paper trading.",
        })
        return {"passed": False, "checks": checks, "backtest_id": None}

    bt = (
        Backtest.objects.filter(
            strategy=strategy,
            status=Backtest.DONE,
            data_era=Backtest.ERA_TOTAL_RETURN,
        )
        .order_by("-created_at")
        .first()
    )
    if bt is None:
        checks.append({
            "key": "has_backtest", "ok": False,
            "detail": "Run a passing walk-forward backtest for this strategy first "
                      "(synthetic seeds and price-only-era runs don't count).",
        })
        return {"passed": False, "checks": checks, "backtest_id": None}
    checks.append({"key": "has_backtest", "ok": True, "detail": f"Backtest #{bt.id}"})

    metrics = BacktestMetrics.objects.filter(backtest=bt).first()
    oos = Decimal(str(metrics.mean_oos_sharpe)) if metrics else Decimal("0")
    stitched = Decimal(str(metrics.sharpe)) if metrics else Decimal("0")
    max_dd = abs(Decimal(str(metrics.max_drawdown_pct))) if metrics else Decimal("999")
    hard = _hard_halt_pct(strategy)

    checks.append({
        "key": "positive_oos_sharpe", "ok": oos > 0,
        "detail": f"OOS Sharpe {oos} (need > 0)",
    })
    checks.append({
        "key": "positive_stitched_sharpe", "ok": stitched > 0,
        "detail": f"Stitched OOS Sharpe {stitched} (need > 0; the long-run number, "
                  "not the upward-biased mean of folds)",
    })
    checks.append({
        "key": "drawdown_within_limit", "ok": max_dd <= hard,
        "detail": f"Backtest max DD {max_dd}% (limit {hard}%)",
    })

    # Staleness: the qualifying backtest must be newer than the last config edit.
    edited = getattr(strategy, "updated_at", None)
    fresh = edited is None or bt.created_at >= edited
    checks.append({
        "key": "backtest_fresh", "ok": fresh,
        "detail": "Backtest predates the strategy's last edit — re-run it."
        if not fresh else "Backtest reflects the current config.",
    })

    passed = all(c["ok"] for c in checks)
    return {"passed": passed, "checks": checks, "backtest_id": bt.id}
