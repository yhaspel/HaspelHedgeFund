"""Wave 3 / WP P3 — expected (backtest) vs realized (live) per strategy cycle.

The question this answers
------------------------
A strategy is armed on the strength of a walk-forward backtest. Every week it
then trades a live cycle. Nothing in the product ever asked the only question
that matters afterwards: **is what the pod is doing live inside the range the
backtest said to expect, or has it left the distribution?**

The pieces already existed and are only joined here:

* the §9 validation ``Backtest`` (``apps.portfolios.validation``) and its
  ``BacktestFold`` rows — each fold is an out-of-sample window with its own
  return, Sharpe and (when the run persisted them) a daily ``BacktestDay``
  equity series;
* the live cycles (``PortfolioTarget``) and the **disjoint** per-period return
  convention WP-P1 built in ``apps.leaderboard.period_returns`` — imported, not
  reimplemented, so this module and the leaderboard can never disagree about
  what a cycle "returned".

For each DONE cycle we find the fold whose OOS window covers the cycle's
``as_of_date``, place the cycle's realized period return inside that fold's OOS
return distribution (z-score + percentile), and flag the ones that fall outside
it.

What this module refuses to do
------------------------------
* invent an expectation from a backtest that does not cover the cycle's window
  — a non-covering fold is reported as ``covers: false`` with the calendar gap
  and the whole payload turns ``provisional``;
* quote a ratio off a handful of cycles — under ``MIN_SCORED_CYCLES`` scored
  cycles every summary ratio is ``null`` (the same contract
  ``apps.leaderboard.compute`` uses for a provisional scorecard);
* pretend a fold with no persisted daily series has a distribution — the
  expectation is then pro-rated from the fold's total OOS return and the
  z-score / percentile stay ``null``.

Statistical honesty notes
-------------------------
* The realized return covers a calendar interval; the fold's distribution is
  daily. The interval is converted to a session count with the standard
  252/365.25 factor (``sessions_assumed`` on every row) and the expectation is
  compounded over it; the standard deviation is scaled by ``√sessions``. That
  is an iid approximation and is labelled as such.
* The empirical percentile uses OVERLAPPING n-session windows inside the fold.
  Overlapping windows are not independent observations — which is exactly why
  WP-P1 refused to feed them to a Sharpe — but a percentile is a **rank**, not
  a ratio, and the overlap costs precision rather than validity. When too few
  windows exist the percentile falls back to the normal CDF of the z-score and
  says so in ``percentile_method``.
"""
from __future__ import annotations

import datetime as dt
import math
import statistics
from dataclasses import dataclass

from django.utils import timezone

from apps.leaderboard.period_returns import Period, period_returns

from .models import PortfolioTarget
from .validation import validation_status

# Below this many SCORED cycles the summary quotes no ratio (mirrors the
# leaderboard's provisional contract: a sample this small cannot support one).
MIN_SCORED_CYCLES = 5

# |z| beyond this is "outside the distribution the backtest described".
Z_OUTSIDE = 2.0

# Fewest overlapping windows before an empirical percentile beats the normal
# approximation. Below it the rank is dominated by a couple of points.
MIN_EMPIRICAL_WINDOWS = 5

TRADING_DAYS_PER_YEAR = 252.0
CALENDAR_DAYS_PER_YEAR = 365.25


def sessions_in(calendar_days: int) -> int:
    """Trading sessions in a calendar interval (252/365.25), floored at 1."""
    if calendar_days <= 0:
        return 1
    return max(1, int(round(calendar_days * TRADING_DAYS_PER_YEAR / CALENDAR_DAYS_PER_YEAR)))


@dataclass(frozen=True)
class FoldDistribution:
    """A fold's out-of-sample daily-return distribution."""

    returns: tuple[float, ...]
    mean: float
    stdev: float

    @property
    def n(self) -> int:
        return len(self.returns)


def _distribution(values: list[float]) -> FoldDistribution | None:
    """Daily returns from a fold's OOS equity series (needs ≥ 3 points)."""
    rets: list[float] = []
    for prev, cur in zip(values, values[1:], strict=False):
        if prev and prev > 0:
            rets.append(cur / prev - 1.0)
    if len(rets) < 2:
        return None
    return FoldDistribution(
        returns=tuple(rets),
        mean=statistics.fmean(rets),
        stdev=statistics.pstdev(rets),
    )


def _fold_distributions(backtest, folds) -> dict[int, FoldDistribution]:
    """``fold_id -> FoldDistribution`` in ONE query over ``BacktestDay``.

    Rows written before per-fold day links carry ``fold_id=None``; those are
    bucketed into the fold whose OOS window contains the day, so an older run
    still yields a distribution instead of silently having none.
    """
    from apps.backtests.models import BacktestDay

    if not folds:
        return {}
    by_fold: dict[int, list[tuple[dt.date, float]]] = {}
    windows = [(f.oos_start, f.oos_end, f.id) for f in folds]
    rows = (
        BacktestDay.objects.filter(backtest=backtest, segment=BacktestDay.SEG_OOS)
        .order_by("date")
        .values_list("fold_id", "date", "portfolio_value")
    )
    for fold_id, day, value in rows.iterator():
        if value is None:
            continue
        if fold_id is None:
            fold_id = next(
                (fid for start, end, fid in windows if start <= day <= end), None,
            )
            if fold_id is None:
                continue
        by_fold.setdefault(fold_id, []).append((day, float(value)))
    out: dict[int, FoldDistribution] = {}
    for fold_id, points in by_fold.items():
        points.sort()
        dist = _distribution([v for _, v in points])
        if dist is not None:
            out[fold_id] = dist
    return out


def _gap_days(fold, as_of: dt.date) -> int:
    """Calendar days between ``as_of`` and the fold's OOS window (0 = inside)."""
    if as_of < fold.oos_start:
        return (fold.oos_start - as_of).days
    if as_of > fold.oos_end:
        return (as_of - fold.oos_end).days
    return 0


def _select_fold(folds, as_of: dt.date):
    """``(fold, covers, gap_days)`` — the covering fold, else the nearest one."""
    if not folds:
        return None, False, None
    covering = [f for f in folds if f.oos_start <= as_of <= f.oos_end]
    if covering:
        # Walk-forward windows can overlap; the latest one is the most recent
        # evidence about that date.
        return max(covering, key=lambda f: f.fold_index), True, 0
    nearest = min(folds, key=lambda f: (_gap_days(f, as_of), f.fold_index))
    return nearest, False, _gap_days(nearest, as_of)


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _overlapping_windows(returns: tuple[float, ...], sessions: int) -> list[float]:
    """Compounded returns of every rolling ``sessions``-long window."""
    if sessions <= 0 or len(returns) < sessions:
        return []
    out: list[float] = []
    for i in range(len(returns) - sessions + 1):
        acc = 1.0
        for r in returns[i:i + sessions]:
            acc *= 1.0 + r
        out.append(acc - 1.0)
    return out


def _fold_expected_from_total(fold, sessions: int) -> float | None:
    """Pro-rate a fold's TOTAL OOS return over ``sessions`` sessions.

    The fallback when a fold has no persisted daily series: the fold really did
    return X% over its window, so the per-session rate implied by it is a real
    (if coarse) expectation. It carries no dispersion, so no z-score is derived
    from it.
    """
    span = (fold.oos_end - fold.oos_start).days
    fold_sessions = sessions_in(span)
    if fold_sessions <= 0:
        return None
    total = float(fold.oos_return_pct or 0) / 100.0
    if total <= -1.0:
        return None
    daily = (1.0 + total) ** (1.0 / fold_sessions) - 1.0
    return (1.0 + daily) ** sessions - 1.0


def _round(value: float | None, places: int = 4) -> float | None:
    return None if value is None else round(value, places)


def _score_period(
    period: Period, fold, covers: bool, gap: int | None,
    dist: FoldDistribution | None,
) -> dict:
    """Place one cycle's realized return inside its fold's OOS distribution."""
    sessions = sessions_in(period.days)
    realized_pct = period.ret * 100.0
    row: dict = {
        "sessions_assumed": sessions,
        "realized_return_pct": _round(realized_pct),
        "expected_return_pct": None,
        "expected_sd_pp": None,
        "expected_source": None,
        "z_score": None,
        "percentile": None,
        "percentile_method": "unavailable",
        "outside_distribution": False,
        "sample_size": 0,
        "fold": {
            "index": fold.fold_index,
            "id": fold.id,
            "oos_start": fold.oos_start.isoformat(),
            "oos_end": fold.oos_end.isoformat(),
            "oos_return_pct": _round(float(fold.oos_return_pct or 0)),
            "oos_sharpe": _round(float(fold.oos_sharpe or 0)),
            "covers": covers,
            "gap_days": gap,
        },
    }
    prefix = ""
    if not covers:
        prefix = (
            f"No validation fold covers {period.start.isoformat()} — compared against the "
            f"nearest fold #{fold.fold_index}, {gap} calendar day(s) away. Indicative only. "
        )

    if dist is None:
        expected = _fold_expected_from_total(fold, sessions)
        if expected is None:
            row["note"] = (
                f"{prefix}Fold #{fold.fold_index} has no usable out-of-sample series, so this "
                "cycle has no expectation to be measured against."
            )
            return row
        row["expected_return_pct"] = _round(expected * 100.0)
        row["expected_source"] = "fold_total"
        row["note"] = (
            f"{prefix}Realized {realized_pct:+.2f}% over {period.days} calendar day(s) "
            f"(~{sessions} session(s)) vs {expected * 100:+.2f}% pro-rated from fold "
            f"#{fold.fold_index}'s total out-of-sample return. That fold persisted no daily "
            "series, so there is no distribution to place this cycle in — no z-score."
        )
        return row

    expected = (1.0 + dist.mean) ** sessions - 1.0
    sd = dist.stdev * math.sqrt(sessions)
    row["expected_return_pct"] = _round(expected * 100.0)
    row["expected_sd_pp"] = _round(sd * 100.0)
    row["expected_source"] = "fold_daily"
    row["sample_size"] = dist.n

    realized = period.ret
    z = (realized - expected) / sd if sd > 0 else None
    windows = _overlapping_windows(dist.returns, sessions)
    if len(windows) >= MIN_EMPIRICAL_WINDOWS:
        pct = 100.0 * sum(1 for w in windows if w <= realized) / len(windows)
        method = "empirical"
    elif z is not None:
        pct = 100.0 * _normal_cdf(z)
        method = "normal"
    else:
        pct, method = None, "unavailable"

    row["z_score"] = _round(z)
    row["percentile"] = _round(pct, 2)
    row["percentile_method"] = method
    row["outside_distribution"] = bool(z is not None and abs(z) > Z_OUTSIDE)

    if z is None:
        row["note"] = (
            f"{prefix}Realized {realized_pct:+.2f}% vs fold #{fold.fold_index}'s expectation "
            f"{expected * 100:+.2f}%. That fold's out-of-sample days have zero dispersion, "
            "so no z-score is defined."
        )
        return row
    where = "OUTSIDE" if row["outside_distribution"] else "Inside"
    pct_txt = f", {pct:.0f}th percentile ({method})" if pct is not None else ""
    row["note"] = (
        f"{prefix}Realized {realized_pct:+.2f}% over {period.days} calendar day(s) "
        f"(~{sessions} session(s)) vs fold #{fold.fold_index}'s expectation "
        f"{expected * 100:+.2f}% ± {sd * 100:.2f}pp — z {z:+.2f}{pct_txt}. "
        f"{where} the ±{Z_OUTSIDE:g}σ band the backtest described."
    )
    return row


def _summary(rows: list[dict], provisional: bool) -> dict:
    """Aggregate the scored rows. Every ratio is ``null`` while provisional."""
    scored = [r for r in rows if r["expected_return_pct"] is not None]
    z_scored = [r for r in scored if r["z_score"] is not None]
    outside = [r for r in z_scored if r["outside_distribution"]]
    inside = [r for r in z_scored if not r["outside_distribution"]]
    out = {
        "cycles": len(rows),
        "scored": len(scored),
        "unscored": len(rows) - len(scored),
        "z_scored": len(z_scored),
        "inside": len(inside),
        "outside": len(outside),
        "inside_ratio": None,
        "outside_ratio": None,
        "mean_realized_pct": None,
        "mean_expected_pct": None,
        "mean_gap_pp": None,
        "min_cycles_for_ratios": MIN_SCORED_CYCLES,
        "z_outside_threshold": Z_OUTSIDE,
    }
    if scored:
        # The mean of what the cycles actually did is not an expectation and not
        # a ratio — it is the same realized numbers the rows already carry, so
        # it is reported even when provisional.
        out["mean_realized_pct"] = _round(
            statistics.fmean(r["realized_return_pct"] for r in scored)
        )
    if provisional or not z_scored:
        return out
    out["inside_ratio"] = _round(len(inside) / len(z_scored))
    out["outside_ratio"] = _round(len(outside) / len(z_scored))
    out["mean_expected_pct"] = _round(
        statistics.fmean(r["expected_return_pct"] for r in scored)
    )
    out["mean_gap_pp"] = _round(
        statistics.fmean(
            r["realized_return_pct"] - r["expected_return_pct"] for r in scored
        )
    )
    return out


def expected_vs_realized(strategy, *, end_date: dt.date | None = None) -> dict:
    """Per-cycle expected-vs-realized for one strategy.

    ``end_date`` closes the newest cycle's holding interval (defaults to today);
    it exists so tests and back-dated reports are deterministic.
    """
    end_date = end_date or timezone.localdate()
    gate = validation_status(strategy)
    gate_passed = bool(gate.get("passed"))
    backtest = None
    backtest_id = gate.get("backtest_id")
    if backtest_id:
        from apps.backtests.models import Backtest

        backtest = Backtest.objects.filter(pk=backtest_id).first()

    targets = list(
        PortfolioTarget.objects.filter(
            strategy=strategy, status=PortfolioTarget.DONE,
        ).order_by("as_of_date")
    )
    periods = period_returns(targets, end_date=end_date)
    target_by_date = {t.as_of_date: t for t in targets}

    provisional_reasons: list[str] = []
    if backtest is None:
        provisional_reasons.append(
            "No DONE total-return backtest is linked to this strategy, so there is no "
            "validated expectation to compare against."
        )
    elif not gate_passed:
        provisional_reasons.append(
            "The §9 validation gate does not pass for the linked backtest ("
            + "; ".join(gate.get("reasons") or ["reason unavailable"])
            + ") — its folds are shown as context, not as a validated expectation."
        )

    folds = list(backtest.folds.all().order_by("fold_index")) if backtest else []
    if backtest is not None and not folds:
        provisional_reasons.append(
            f"Backtest #{backtest.id} persisted no walk-forward folds, so no cycle can be "
            "matched to an out-of-sample window."
        )
    distributions = _fold_distributions(backtest, folds) if folds else {}

    rows: list[dict] = []
    uncovered = 0
    for period in periods:
        target = target_by_date.get(period.start)
        fold, covers, gap = _select_fold(folds, period.start)
        base = {
            "target_id": target.id if target is not None else None,
            "as_of_date": period.start.isoformat(),
            "period_start": period.start.isoformat(),
            "period_end": period.end.isoformat(),
            "period_days": period.days,
        }
        if fold is None:
            rows.append({
                **base,
                "sessions_assumed": sessions_in(period.days),
                "realized_return_pct": _round(period.ret * 100.0),
                "expected_return_pct": None,
                "expected_sd_pp": None,
                "expected_source": None,
                "z_score": None,
                "percentile": None,
                "percentile_method": "unavailable",
                "outside_distribution": False,
                "sample_size": 0,
                "fold": None,
                "note": (
                    "No validation backtest fold exists for this strategy — the realized "
                    "return is shown with no expectation attached."
                ),
            })
            uncovered += 1
            continue
        if not covers:
            uncovered += 1
        rows.append({
            **base,
            **_score_period(period, fold, covers, gap, distributions.get(fold.id)),
        })

    if uncovered:
        provisional_reasons.append(
            f"{uncovered} of {len(rows)} cycle(s) fall outside every out-of-sample fold "
            "window — those rows are matched to the nearest fold and are indicative only."
        )
    scored = sum(1 for r in rows if r["expected_return_pct"] is not None)
    if scored < MIN_SCORED_CYCLES:
        provisional_reasons.append(
            f"{scored} scored cycle(s) — fewer than the {MIN_SCORED_CYCLES} needed before a "
            "ratio means anything."
        )
    provisional = bool(provisional_reasons)

    return {
        "strategy_id": strategy.id,
        "strategy_name": strategy.name,
        "end_date": end_date.isoformat(),
        "provisional": provisional,
        "provisional_reasons": provisional_reasons,
        "backtest": None if backtest is None else {
            "id": backtest.id,
            "name": backtest.name,
            "status": backtest.status,
            "engine_mode": backtest.engine_mode,
            "engine_version": backtest.engine_version,
            "data_era": backtest.data_era,
            "gate_passed": gate_passed,
            "gate_reasons": list(gate.get("reasons") or []),
            "gate_warnings": list(gate.get("warnings") or []),
            "folds": len(folds),
            "oos_start": folds[0].oos_start.isoformat() if folds else None,
            "oos_end": folds[-1].oos_end.isoformat() if folds else None,
        },
        "cycles": rows,
        "summary": _summary(rows, provisional),
    }
