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

**Evidence-by-association was the hole** (review F18 / G1): every field the gate
reads is a *metric*, and none of them said the backtest actually modelled THIS
strategy. Universe, engine mode and the walk-forward window are all
caller-supplied on ``POST /api/backtests/``, so a run of a different universe,
on the council engine, over a cherry-picked 18-month window with one 5-day OOS
fold armed a live pod. ``enable_gate`` adds the structural checks:

  * the backtest's ``universe`` is set-equal to the strategy's active universe,
  * its ``engine_mode`` is the one the strategy's kind trades live on,
  * ≥ ``MIN_FOLDS`` walk-forward folds and ≥ ``MIN_OOS_SESSIONS`` OOS sessions.

Owner decision (2026-09): these are **strict for a new enable** and **advisory
for an already-enabled autopilot** — a running pod is never auto-disabled by a
gate change; the same checks surface as ``validation_warnings`` on the fund /
member payload instead.
"""
from __future__ import annotations

from decimal import Decimal

# Structural minimums for gate-grade evidence (see the module docstring).
MIN_FOLDS = 6
MIN_OOS_SESSIONS = 120


def _hard_halt_pct(strategy) -> Decimal:
    ap = getattr(strategy, "autopilot", None)
    return ap.dd_hard_halt_pct if (ap and ap.dd_hard_halt_pct) else Decimal("7.5")


def _strategy_universe(strategy) -> set[str]:
    """The strategy's CURRENT active universe tickers (uppercased)."""
    from .models import UniverseMembership

    if strategy.universe_id is None:
        return set()
    return {
        t.upper()
        for t in UniverseMembership.objects.filter(
            universe_id=strategy.universe_id, effective_to__isnull=True,
        ).values_list("ticker", flat=True)
    }


def expected_engine_mode(strategy) -> str:
    """The backtest engine that models how this strategy trades live. Mirrors
    the auto-routing in ``apps.backtests.serializers.BacktestCreateSerializer``
    so a run created through the UI matches by construction."""
    from apps.backtests.models import Backtest

    from .models import PortfolioStrategy

    return {
        PortfolioStrategy.KIND_RISK_PARITY: Backtest.RISK_PARITY,
        PortfolioStrategy.KIND_TREND: Backtest.TREND,
        PortfolioStrategy.KIND_SECTOR_MOMENTUM: Backtest.SECTOR_MOMENTUM,
        PortfolioStrategy.KIND_XSEC_LONG_SHORT: Backtest.XSEC_LONG_SHORT,
    }.get(strategy.kind, Backtest.COUNCIL)


def _fold_shape(bt) -> tuple[int, int]:
    """``(n_folds, n_oos_sessions)`` for a backtest.

    Prefers the persisted fold / day rows (the truth for a real run); falls back
    to the walk-forward window the run was CONFIGURED for when a row carries no
    per-fold detail, so the check is never silently skipped."""
    n_folds = bt.folds.count()
    n_oos = bt.days.filter(segment="oos").count()
    if n_folds == 0:
        span = (bt.end_date - bt.start_date).days
        step = max(1, int(bt.step_days or 1))
        usable = span - int(bt.is_window_days or 0)
        n_folds = (usable // step) + 1 if usable >= 0 else 0
    if n_oos == 0:
        n_oos = n_folds * max(0, int(bt.oos_window_days or 0))
    return n_folds, n_oos


def _structural_checks(strategy, bt) -> list[dict]:
    """The F18/G1 evidence-fit checks: does this backtest model THIS strategy?"""
    bt_universe = {str(t).upper() for t in (bt.universe or [])}
    s_universe = _strategy_universe(strategy)
    expected_engine = expected_engine_mode(strategy)
    n_folds, n_oos = _fold_shape(bt)
    return [
        {
            "key": "universe_matches",
            "ok": bool(bt_universe) and bt_universe == s_universe,
            "detail": (
                "Backtest universe matches the strategy's."
                if bt_universe and bt_universe == s_universe else
                f"Backtest universe {sorted(bt_universe) or '(empty)'} is not the "
                f"strategy's universe {sorted(s_universe) or '(empty)'} — re-run the "
                "validation backtest on the strategy it validates."
            ),
        },
        {
            "key": "engine_matches_kind",
            "ok": bt.engine_mode == expected_engine,
            "detail": (
                f"Engine {bt.engine_mode} models a {strategy.kind} strategy."
                if bt.engine_mode == expected_engine else
                f"Backtest ran on the {bt.engine_mode} engine; a {strategy.kind} "
                f"strategy trades live on {expected_engine}."
            ),
        },
        {
            "key": "enough_folds",
            "ok": n_folds >= MIN_FOLDS,
            "detail": f"{n_folds} walk-forward fold(s) (need ≥ {MIN_FOLDS})",
        },
        {
            "key": "enough_oos_sessions",
            "ok": n_oos >= MIN_OOS_SESSIONS,
            "detail": f"{n_oos} out-of-sample session(s) (need ≥ {MIN_OOS_SESSIONS})",
        },
    ]


def _reasons(checks: list[dict]) -> list[str]:
    return [c["detail"] for c in checks if not c["ok"]]


def validation_status(strategy) -> dict:
    """Return the §9 checklist for the strategy's autopilot enable toggle:
    ``{passed, checks, reasons, warnings, backtest_id}``.

    ``passed``/``checks`` are the METRIC checks (unchanged shape). ``warnings``
    are the structural evidence-fit checks that fail — advisory here, blocking
    in :func:`enable_gate` (owner decision: strict for new enables, warnings
    only for an already-running pod)."""
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
        return {
            "passed": False, "checks": checks, "reasons": _reasons(checks),
            "warnings": [], "backtest_id": None,
        }

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
        return {
            "passed": False, "checks": checks, "reasons": _reasons(checks),
            "warnings": [
                "No DONE total-return backtest — nothing to check the universe, "
                "engine or walk-forward shape against.",
            ],
            "backtest_id": None,
        }
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
    structural = _structural_checks(strategy, bt)
    return {
        "passed": passed,
        "checks": checks,
        "reasons": _reasons(checks),
        "warnings": _reasons(structural),
        "backtest_id": bt.id,
    }


def enable_gate(strategy) -> dict:
    """The **blocking** §9 gate for arming an autopilot (a NEW enable).

    ``validation_status``'s metric checks PLUS the structural evidence-fit
    checks — the backtest must be of this strategy's universe, on the engine
    this kind trades live on, and long enough to mean anything
    (≥ ``MIN_FOLDS`` folds, ≥ ``MIN_OOS_SESSIONS`` OOS sessions).

    Returns ``{passed, reasons, warnings, checks, backtest_id}``. An
    already-enabled autopilot is NEVER auto-disabled by this — the same checks
    reach the fund/member payload as non-blocking ``validation_warnings``.
    """
    base = validation_status(strategy)
    if base["backtest_id"] is None:
        return {**base, "reasons": _reasons(base["checks"]) + base["warnings"]}

    from apps.backtests.models import Backtest

    bt = Backtest.objects.filter(pk=base["backtest_id"]).first()
    structural = _structural_checks(strategy, bt) if bt is not None else []
    checks = [*base["checks"], *structural]
    return {
        "passed": all(c["ok"] for c in checks),
        "checks": checks,
        "reasons": _reasons(checks),
        "warnings": _reasons(structural),
        "backtest_id": base["backtest_id"],
    }
