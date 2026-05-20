"""Portfolio Constructor — deterministic, no LLM.

Input:
  - council_decisions: list of per-candidate council outputs
        [{ticker, sector, side: "long"|"short", action, confidence (0-100),
          quality_weight (defaults 1.0), veto_reason or None}]
  - constraints: target_gross_pct, target_net_pct, max_position_pct,
                 max_sector_pct, min_position_pct
Output:
  - dict {target_weights, gross_pct, net_pct, sector_exposure, rejected}
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass
class Candidate:
    ticker: str
    sector: str
    side: str  # "long" | "short"
    action: str
    confidence: int  # 0..100
    quality_weight: float = 1.0
    veto_reason: str | None = None


@dataclass
class Constraints:
    target_gross_pct: float = 1.50
    target_net_pct: float = 0.50
    max_position_pct: float = 0.03
    max_sector_pct: float = 0.25
    min_position_pct: float = 0.005


@dataclass
class ConstructorResult:
    target_weights: dict[str, float]
    gross_pct: float
    net_pct: float
    sector_exposure: dict[str, float]
    rejected: list[dict]


def _filter_actionable(cands: Iterable[Candidate]) -> tuple[list[Candidate], list[dict]]:
    keep: list[Candidate] = []
    rejected: list[dict] = []
    for c in cands:
        if c.veto_reason:
            rejected.append({"ticker": c.ticker, "reason": c.veto_reason})
            continue
        if c.action not in {"buy", "open_short"}:
            rejected.append({"ticker": c.ticker, "reason": f"action={c.action}"})
            continue
        if c.action == "buy" and c.side != "long":
            rejected.append({"ticker": c.ticker, "reason": "buy on non-long side"})
            continue
        if c.action == "open_short" and c.side != "short":
            rejected.append({"ticker": c.ticker, "reason": "open_short on non-short side"})
            continue
        keep.append(c)
    return keep, rejected


def _bucket_weights(bucket: list[Candidate], total_pct: float) -> dict[str, float]:
    if not bucket or total_pct <= 0:
        return {}
    raw = {c.ticker: max(1e-6, (c.confidence / 100.0) * c.quality_weight) for c in bucket}
    s = sum(raw.values())
    return {t: (v / s) * total_pct for t, v in raw.items()}


def _apply_per_name_cap(weights: dict[str, float], cap: float) -> dict[str, float]:
    if not weights:
        return weights
    overflow = 0.0
    capped: dict[str, float] = {}
    under: list[str] = []
    for t, w in weights.items():
        sign = 1.0 if w >= 0 else -1.0
        mag = abs(w)
        if mag > cap:
            overflow += (mag - cap)
            capped[t] = cap * sign
        else:
            capped[t] = w
            under.append(t)
    if overflow > 1e-9 and under:
        # Redistribute among names that still have headroom (one pass).
        room = {t: (cap - abs(capped[t])) for t in under}
        room_total = sum(room.values())
        if room_total > 1e-9:
            for t in under:
                share = (room[t] / room_total) * overflow
                sign = 1.0 if capped[t] >= 0 else -1.0
                capped[t] += share * sign
    return capped


def _apply_sector_cap(
    weights: dict[str, float], sector_of: dict[str, str], cap: float
) -> tuple[dict[str, float], list[dict]]:
    if not weights:
        return weights, []
    by_sector: dict[str, float] = {}
    for t, w in weights.items():
        by_sector.setdefault(sector_of.get(t, ""), 0.0)
        by_sector[sector_of.get(t, "")] += abs(w)
    breaches = [s for s, m in by_sector.items() if m > cap + 1e-9]
    notes: list[dict] = []
    if not breaches:
        return weights, notes
    out = dict(weights)
    for s in breaches:
        sec_total = by_sector[s]
        scale = cap / sec_total
        for t, w in list(out.items()):
            if sector_of.get(t, "") == s:
                out[t] = w * scale
        notes.append({"sector": s, "scaled_from": sec_total, "to": cap})
    return out, notes


def construct(
    candidates: list[Candidate], constraints: Constraints
) -> ConstructorResult:
    actionable, rejected = _filter_actionable(candidates)
    longs = [c for c in actionable if c.action == "buy"]
    shorts = [c for c in actionable if c.action == "open_short"]

    gross = constraints.target_gross_pct
    net = constraints.target_net_pct
    target_long = (gross + net) / 2.0
    target_short = (gross - net) / 2.0
    target_short = max(0.0, target_short)

    long_w = _bucket_weights(longs, target_long)
    short_w_pos = _bucket_weights(shorts, target_short)
    short_w = {t: -w for t, w in short_w_pos.items()}

    weights = {**long_w, **short_w}
    weights = _apply_per_name_cap(weights, constraints.max_position_pct)

    sector_of = {c.ticker: c.sector for c in actionable}
    weights, sector_notes = _apply_sector_cap(weights, sector_of, constraints.max_sector_pct)
    for n in sector_notes:
        rejected.append({"reason": "sector_scaled", **n})

    # Drop sub-threshold weights.
    weights = {
        t: w for t, w in weights.items() if abs(w) >= constraints.min_position_pct
    }
    for c in actionable:
        if c.ticker not in weights and not any(
            r.get("ticker") == c.ticker for r in rejected
        ):
            rejected.append({"ticker": c.ticker, "reason": "below_min_position"})

    sector_exposure: dict[str, float] = {}
    for t, w in weights.items():
        s = sector_of.get(t, "")
        sector_exposure[s] = sector_exposure.get(s, 0.0) + w

    gross_pct = sum(abs(w) for w in weights.values())
    net_pct = sum(weights.values())

    return ConstructorResult(
        target_weights=weights,
        gross_pct=gross_pct,
        net_pct=net_pct,
        sector_exposure=sector_exposure,
        rejected=rejected,
    )


class PMActionWhitelistError(ValueError):
    """Raised when PM emits an action that's not allowed in a flavor."""


@dataclass
class ConcentratedResult:
    target_weights: dict[str, float]
    gross_pct: float
    net_pct: float
    sector_exposure: dict[str, float]
    rejected: list[dict]
    outcome: str  # "target_created" | "held_existing_book"


_CONCENTRATED_LONG_ALLOWED = {"buy", "hold"}


def construct_concentrated_long(
    candidates: list[Candidate],
    constraints: Constraints,
    *,
    min_positions: int = 5,
    max_positions: int = 15,
    min_aggregate_confidence: float = 0.65,
) -> ConcentratedResult:
    """Concentrated long-only construction.

    Steps:
      1. Hard whitelist: any short-side decision or open_short/cover_short action raises.
      2. Drop holds and short candidates.
      3. Drop candidates below min_aggregate_confidence (0..1, vs confidence 0..100).
      4. Sort by confidence × quality, take top max_positions.
      5. If survivors < min_positions: return outcome=held_existing_book, no weights.
      6. Weight by confidence × quality, normalise to target_gross_pct.
      7. Apply per-name cap with overflow redistribution.
      8. Apply min_position_pct floor: weights below floor are raised to floor;
         excess is taken proportionally from the largest positions.
      9. Optional sector cap.
    """
    # Step 1 — whitelist guard. open_short / cover_short here is a programming
    # error in the cycle dispatcher, not a candidate rejection.
    for c in candidates:
        if c.action in {"open_short", "cover_short"} or c.side == "short":
            raise PMActionWhitelistError(
                f"concentrated_long flavor cannot accept {c.action!r} on {c.ticker} "
                f"(side={c.side!r})"
            )

    rejected: list[dict] = []
    survivors: list[Candidate] = []
    threshold_int = int(round(min_aggregate_confidence * 100))
    for c in candidates:
        if c.veto_reason:
            rejected.append({"ticker": c.ticker, "reason": c.veto_reason})
            continue
        if c.action != "buy":
            rejected.append({"ticker": c.ticker, "reason": f"action={c.action}"})
            continue
        if c.confidence < threshold_int:
            rejected.append({
                "ticker": c.ticker,
                "reason": f"below_confidence_threshold ({c.confidence} < {threshold_int})",
            })
            continue
        survivors.append(c)

    survivors.sort(key=lambda c: c.confidence * c.quality_weight, reverse=True)
    # Hard ceiling implied by the floor: max names that can each clear floor.
    if constraints.min_position_pct > 0:
        floor_cap = int(constraints.target_gross_pct / constraints.min_position_pct)
        max_positions = min(max_positions, max(1, floor_cap))
    survivors = survivors[:max_positions]

    if len(survivors) < min_positions:
        rejected.append({
            "reason": "insufficient_high_conviction_candidates",
            "n_survivors": len(survivors),
            "min_required": min_positions,
        })
        return ConcentratedResult(
            target_weights={},
            gross_pct=0.0,
            net_pct=0.0,
            sector_exposure={},
            rejected=rejected,
            outcome="held_existing_book",
        )

    target_total = constraints.target_gross_pct
    weights = _bucket_weights(survivors, target_total)

    cap = constraints.max_position_pct
    weights = _apply_per_name_cap(weights, cap)

    floor = constraints.min_position_pct
    if floor > 0 and weights:
        below = {t: w for t, w in weights.items() if 0 < w < floor}
        if below:
            extra_needed = sum(floor - w for w in below.values())
            for t in below:
                weights[t] = floor
            donors = {t: w for t, w in weights.items() if w > floor and t not in below}
            donor_total = sum(donors.values())
            if donor_total > extra_needed:
                for t in donors:
                    share = (donors[t] / donor_total) * extra_needed
                    weights[t] = max(floor, weights[t] - share)

    sector_of = {c.ticker: c.sector for c in survivors}
    if constraints.max_sector_pct and constraints.max_sector_pct > 0:
        weights, _notes = _apply_sector_cap(weights, sector_of, constraints.max_sector_pct)

    sector_exposure: dict[str, float] = {}
    for t, w in weights.items():
        s = sector_of.get(t, "")
        sector_exposure[s] = sector_exposure.get(s, 0.0) + w

    gross_pct = sum(abs(w) for w in weights.values())
    net_pct = sum(weights.values())

    return ConcentratedResult(
        target_weights=weights,
        gross_pct=gross_pct,
        net_pct=net_pct,
        sector_exposure=sector_exposure,
        rejected=rejected,
        outcome="target_created",
    )


@dataclass
class SectorRotationResult:
    target_weights: dict[str, float]
    gross_pct: float
    net_pct: float
    sector_exposure: dict[str, float]
    rejected: list[dict]
    overlap_dropped: list[dict]


# ETF holdings overlap matrix. ~50% holdings overlap = implicit double bet.
# Hard-coded for the initial registry — replace with a real holdings snapshot
# in P3+. Pairs map (ETF_A, ETF_B) → estimated holdings overlap fraction.
_OVERLAP_PAIRS: dict[tuple[str, str], float] = {
    ("XLK", "SOXX"): 0.50,
    ("XLK", "XLC"): 0.20,
    ("XLF", "KRE"): 0.45,
    ("XLE", "XOP"): 0.55,
    ("XLV", "IBB"): 0.40,
    ("XLY", "ITB"): 0.30,
}


def _overlap_fraction(a: str, b: str) -> float:
    return _OVERLAP_PAIRS.get((a, b)) or _OVERLAP_PAIRS.get((b, a)) or 0.0


def construct_sector_rotation(
    candidates: list[Candidate],
    *,
    target_gross_pct: float = 1.00,
    per_etf_max_pct: float = 0.30,
    per_etf_min_pct: float = 0.05,
    max_etfs_held: int = 6,
    overlap_threshold: float = 0.40,
) -> SectorRotationResult:
    """Long-only ETF allocation across sectors.

    PM action whitelist: only `buy` survives; `hold` and short actions are
    rejected. Vetoes propagate as in single-name long-only.
    """
    rejected: list[dict] = []
    survivors: list[Candidate] = []
    for c in candidates:
        if c.action in {"open_short", "cover_short"} or c.side == "short":
            raise PMActionWhitelistError(
                f"sector_rotation cannot accept {c.action!r} on {c.ticker}"
            )
        if c.veto_reason:
            rejected.append({"ticker": c.ticker, "reason": c.veto_reason})
            continue
        if c.action != "buy":
            rejected.append({"ticker": c.ticker, "reason": f"action={c.action}"})
            continue
        survivors.append(c)

    # Sort by conviction.
    survivors.sort(key=lambda c: c.confidence * c.quality_weight, reverse=True)

    # Overlap penalty: drop the lower-conviction member of any high-overlap pair.
    keep: list[Candidate] = []
    overlap_dropped: list[dict] = []
    for c in survivors:
        clash = next(
            (
                k for k in keep
                if _overlap_fraction(c.ticker, k.ticker) >= overlap_threshold
            ),
            None,
        )
        if clash:
            overlap_dropped.append({
                "ticker": c.ticker,
                "kept": clash.ticker,
                "overlap": _overlap_fraction(c.ticker, clash.ticker),
            })
            rejected.append({
                "ticker": c.ticker,
                "reason": f"holdings_overlap_with_{clash.ticker}",
            })
            continue
        keep.append(c)

    # Enforce position-count ceiling (floor-implied + user max).
    if per_etf_min_pct > 0:
        floor_cap = int(target_gross_pct / per_etf_min_pct)
        max_etfs_held = min(max_etfs_held, max(1, floor_cap))
    keep = keep[:max_etfs_held]

    if not keep:
        return SectorRotationResult(
            target_weights={}, gross_pct=0.0, net_pct=0.0,
            sector_exposure={}, rejected=rejected, overlap_dropped=overlap_dropped,
        )

    weights = _bucket_weights(keep, target_gross_pct)
    weights = _apply_per_name_cap(weights, per_etf_max_pct)

    # Floor enforcement: raise sub-floor weights to floor and pull proportionally
    # from above-floor donors.
    if per_etf_min_pct > 0 and weights:
        below = {t: w for t, w in weights.items() if 0 < w < per_etf_min_pct}
        if below:
            extra = sum(per_etf_min_pct - w for w in below.values())
            for t in below:
                weights[t] = per_etf_min_pct
            donors = {t: w for t, w in weights.items() if w > per_etf_min_pct and t not in below}
            donor_total = sum(donors.values())
            if donor_total > extra:
                for t in donors:
                    share = (donors[t] / donor_total) * extra
                    weights[t] = max(per_etf_min_pct, weights[t] - share)

    # In sector rotation each ETF IS a sector → sector_exposure mirrors weights.
    sector_exposure = dict(weights)
    gross_pct = sum(abs(w) for w in weights.values())
    net_pct = sum(weights.values())

    return SectorRotationResult(
        target_weights=weights,
        gross_pct=gross_pct,
        net_pct=net_pct,
        sector_exposure=sector_exposure,
        rejected=rejected,
        overlap_dropped=overlap_dropped,
    )


@dataclass
class NeutralResult:
    target_weights: dict[str, float]
    gross_pct: float
    net_pct: float
    sector_exposure: dict[str, float]
    rejected: list[dict]
    portfolio_beta: float
    diagnostics: dict


_ALPHA_MIN = 0.7
_ALPHA_MAX = 1.4


def _portfolio_beta(weights: dict[str, float], betas: dict[str, float]) -> float:
    return sum(betas.get(t, 1.0) * w for t, w in weights.items())


def _bucket_beta(weights: dict[str, float], betas: dict[str, float]) -> float:
    return sum(betas.get(t, 1.0) * abs(w) for t, w in weights.items())


def construct_market_neutral(
    candidates: list[Candidate],
    constraints: Constraints,
    betas: dict[str, float],
    *,
    tol_dollar: float = 0.02,
    tol_beta: float = 0.05,
) -> NeutralResult:
    """Dollar- and beta-neutral construction.

    Step 1: build the long/short books with target_net_pct=0 so dollar-
    neutral by construction.
    Step 2: one-knob α ∈ [0.7, 1.4] rescale: longs ← α·longs, shorts ← shorts/α
    so the weighted long-bucket beta and short-bucket beta cancel.
    Step 3: re-apply per-name + sector caps. One repeat at most.
    """
    forced = Constraints(
        target_gross_pct=constraints.target_gross_pct,
        target_net_pct=0.0,
        max_position_pct=constraints.max_position_pct,
        max_sector_pct=constraints.max_sector_pct,
        min_position_pct=constraints.min_position_pct,
    )
    base = construct(candidates, forced)

    weights = dict(base.target_weights)
    longs = {t: w for t, w in weights.items() if w > 0}
    shorts = {t: w for t, w in weights.items() if w < 0}

    diagnostics: dict = {"alpha_clamped": False, "iterations": 0, "unreliable": []}

    def _rescale(longs_in: dict, shorts_in: dict) -> tuple[dict, dict, float]:
        # bL = Σ β_i · |w_i| on longs (since w_i > 0). bS likewise on shorts.
        bL = _bucket_beta(longs_in, betas)
        bS = _bucket_beta(shorts_in, betas)
        if bL <= 0 or bS <= 0:
            return longs_in, shorts_in, 1.0
        # Solve α·bL = bS/α  →  α = sqrt(bS / bL).
        alpha = math.sqrt(bS / bL)
        clamped = False
        if alpha < _ALPHA_MIN:
            alpha = _ALPHA_MIN
            clamped = True
        elif alpha > _ALPHA_MAX:
            alpha = _ALPHA_MAX
            clamped = True
        if clamped:
            diagnostics["alpha_clamped"] = True
        new_longs = {t: w * alpha for t, w in longs_in.items()}
        new_shorts = {t: w / alpha for t, w in shorts_in.items()}
        return new_longs, new_shorts, alpha

    sector_of = {c.ticker: c.sector for c in candidates}

    for iteration in range(2):
        diagnostics["iterations"] = iteration + 1
        if not longs or not shorts:
            break
        longs, shorts, _alpha = _rescale(longs, shorts)
        merged = {**longs, **shorts}
        merged = _apply_per_name_cap(merged, constraints.max_position_pct)
        merged, _notes = _apply_sector_cap(merged, sector_of, constraints.max_sector_pct)
        longs = {t: w for t, w in merged.items() if w > 0}
        shorts = {t: w for t, w in merged.items() if w < 0}
        net_d = abs(sum(merged.values()))
        port_beta = _portfolio_beta(merged, betas)
        if net_d <= tol_dollar and abs(port_beta) <= tol_beta:
            break

    weights = {**longs, **shorts}
    weights = {t: w for t, w in weights.items() if abs(w) >= constraints.min_position_pct}

    sector_exposure: dict[str, float] = {}
    for t, w in weights.items():
        s = sector_of.get(t, "")
        sector_exposure[s] = sector_exposure.get(s, 0.0) + w

    gross_pct = sum(abs(w) for w in weights.values())
    net_pct = sum(weights.values())
    port_beta = _portfolio_beta(weights, betas)

    rejected = list(base.rejected)
    if abs(net_pct) > tol_dollar or abs(port_beta) > tol_beta:
        rejected.append({
            "reason": "neutrality_partial_breach",
            "residual_net_pct": round(net_pct, 6),
            "residual_portfolio_beta": round(port_beta, 4),
            "alpha_clamped": diagnostics["alpha_clamped"],
        })

    return NeutralResult(
        target_weights=weights,
        gross_pct=gross_pct,
        net_pct=net_pct,
        sector_exposure=sector_exposure,
        rejected=rejected,
        portfolio_beta=port_beta,
        diagnostics=diagnostics,
    )
