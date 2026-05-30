"""Council-alpha (P3b): the council-free deterministic baseline per cycle.

Council-alpha = realised strategy return − the return of the *same* cycle's
deterministic, council-free book. Positive ⇒ the LLM council is paying for
itself; persistently zero/negative is a signal to reconsider the config.

The baseline reuses the exact deterministic constructors the live cycle ran
(``apps.portfolios.construction``) but feeds them the screener's composite
score in place of the council's ``aggregate_confidence`` and drops every
council veto — i.e. "what would a no-council, score-driven version of this
strategy have held?". Per the P3b plan's per-flavor baseline definitions:

  - ``sector_rotation`` / ``global_macro`` — top-K *by composite score*,
    score-weighted (the screener score is the only signal anyway).
  - ``long_short`` / ``long_only`` / ``short_only`` / ``concentrated_long`` /
    ``market_neutral`` — equal-weighted top-K from the screener, no veto.
  - ``risk_parity`` / ``pairs`` — already council-free, so baseline == realised
    (alpha ≈ 0 until council-veto mode lands); handled by the cycle itself.

Baseline weights are computed and persisted at cycle finalize time (cheap,
deterministic, $0) on ``PortfolioTarget.baseline_weights``; the nightly
leaderboard recompute marks them with the same forward-return machinery
(``cycle_mark``) and differences the two return series.
"""
from __future__ import annotations

from apps.portfolios.construction import (
    Candidate,
    Constraints,
    construct,
    construct_concentrated_long,
    construct_global_macro,
    construct_market_neutral,
    construct_sector_rotation,
)
from apps.portfolios.models import PortfolioStrategy

# Bump when the deterministic baseline definition changes so historical
# council-alpha series aren't silently mixed across incompatible baselines
# (plan risk #7). Stamped onto ``PortfolioTarget.baseline_version``.
BASELINE_VERSION = "v1"

# Flavors whose live cycle already runs council-free — their baseline is the
# strategy itself, so council-alpha is 0 (until council-veto mode lands).
_DETERMINISTIC_FLAVORS = {
    PortfolioStrategy.KIND_RISK_PARITY,
    PortfolioStrategy.KIND_PAIRS,
}

# Flavors whose baseline tracks the composite score's *magnitude* (vs equal-
# weighting the surviving top-K). These are long-only ETF flavors where the
# screener score is the entire thesis.
_SCORE_WEIGHTED_FLAVORS = {
    PortfolioStrategy.KIND_SECTOR_ROTATION,
    PortfolioStrategy.KIND_GLOBAL_MACRO,
}

# Lowest baseline "confidence" so score-weighting stays strictly positive.
_CONF_FLOOR = 10


def _baseline_candidates(longs, shorts, *, score_weighted: bool) -> list[Candidate]:
    """Council-free ``Candidate`` list from screener candidates: act on every
    screened name (longs→buy, shorts→open_short) with no council veto.

    For score-weighted flavors the screener score is min-max-scaled into a
    positive confidence so the constructor's ``confidence × weight`` is
    monotonic in score; otherwise every survivor gets equal confidence
    (equal-weight top-K), with the screener's existing descending sort
    preserved as the selection order.
    """
    rows = (
        [(c, "long", "buy") for c in (longs or [])]
        + [(c, "short", "open_short") for c in (shorts or [])]
    )
    scores = [float(c.get("score", 0.0) or 0.0) for c, _, _ in rows]
    lo, hi = (min(scores), max(scores)) if scores else (0.0, 0.0)

    def _conf(score: float) -> int:
        if not score_weighted or hi <= lo:
            return 100
        return int(round(_CONF_FLOOR + (100 - _CONF_FLOOR) * (score - lo) / (hi - lo)))

    return [
        Candidate(
            ticker=c["ticker"],
            sector=c.get("sector", "") or "",
            side=side,
            action=action,
            confidence=_conf(float(c.get("score", 0.0) or 0.0)),
            quality_weight=1.0,
            veto_reason=None,
        )
        for c, side, action in rows
    ]


def baseline_target_weights(
    strategy: PortfolioStrategy,
    ranking,
    constraints: Constraints,
    *,
    betas: dict[str, float] | None = None,
    asset_class_of: dict[str, str] | None = None,
    inverse_of: dict[str, str] | None = None,
) -> dict[str, float]:
    """Deterministic council-free target weights for one cycle.

    ``ranking`` is the cycle's ``ScreenerRanking``; ``constraints`` is the SAME
    (regime-scaled) ``Constraints`` the live constructor received, so the only
    difference between live and baseline is the council, not exposure sizing.
    ``betas`` / ``asset_class_of`` / ``inverse_of`` are the maps the live
    market_neutral / global_macro branch already computed — passed through so
    the baseline matches the live constructor's inputs exactly.

    Returns ``{}`` for deterministic flavors (baseline == realised, handled by
    the cycle) and when the screener surfaced no candidates.
    """
    kind = strategy.kind
    if kind in _DETERMINISTIC_FLAVORS:
        return {}

    cands = _baseline_candidates(
        ranking.long_candidates or [],
        ranking.short_candidates or [],
        score_weighted=kind in _SCORE_WEIGHTED_FLAVORS,
    )
    if not cands:
        return {}

    if kind == PortfolioStrategy.KIND_GLOBAL_MACRO:
        result = construct_global_macro(
            cands,
            target_gross_pct=float(strategy.target_gross_pct),
            per_etf_max_pct=float(strategy.per_etf_max_pct),
            per_etf_min_pct=float(strategy.per_etf_min_pct),
            max_etfs_held=int(strategy.max_etfs_held),
            asset_class_caps=strategy.asset_class_caps or None,
            asset_class_of=asset_class_of or {},
            inverse_of=inverse_of or {},
        )
    elif kind == PortfolioStrategy.KIND_SECTOR_ROTATION:
        result = construct_sector_rotation(
            cands,
            target_gross_pct=float(strategy.target_gross_pct),
            per_etf_max_pct=float(strategy.per_etf_max_pct),
            per_etf_min_pct=float(strategy.per_etf_min_pct),
            max_etfs_held=int(strategy.max_etfs_held),
        )
    elif kind == PortfolioStrategy.KIND_CONCENTRATED_LONG:
        # No council ⇒ no aggregate-confidence gate; take the top names by score.
        result = construct_concentrated_long(
            cands,
            constraints,
            min_positions=int(strategy.min_positions),
            max_positions=int(strategy.max_positions),
            min_aggregate_confidence=0.0,
        )
    elif kind == PortfolioStrategy.KIND_MARKET_NEUTRAL:
        result = construct_market_neutral(
            cands,
            constraints,
            betas or {},
            tol_dollar=float(strategy.neutrality_tolerance_dollar_pct),
            tol_beta=float(strategy.neutrality_tolerance_beta),
        )
    else:  # long_short / long_only / short_only → generic L/S constructor
        result = construct(cands, constraints)

    return {t: round(w, 6) for t, w in result.target_weights.items()}
