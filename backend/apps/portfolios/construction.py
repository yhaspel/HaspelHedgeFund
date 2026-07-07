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
from dataclasses import dataclass, field


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
    # P02e review: feasibility diagnostics so the UI can render
    # requested vs achieved exposure and explain underinvestment.
    diagnostics: dict = field(default_factory=dict)


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


# P11 D1 — aggregate equity-class cap for risk parity. Today's "risk parity" is
# ~75% equity risk (6 of 8 seeded sleeves are equity sectors), so the diagonal
# inverse-vol book is far less diversified than the label implies (research §5/R4).
# Classify by an explicit NON-equity allowlist (small + stable) and treat every
# other group as equity — robust to the many, varied equity-sector labels.
NON_EQUITY_SLEEVE_GROUPS = frozenset({
    "rates", "rate", "bond", "bonds", "fixed income", "treasury", "treasuries",
    "duration", "credit", "tips", "commodity", "commodities", "gold",
    "cash", "currency", "currencies", "fx", "real assets",
})


def _is_equity_group(group: str) -> bool:
    return (group or "").strip().lower() not in NON_EQUITY_SLEEVE_GROUPS


def _apply_equity_class_cap(
    weights: dict[str, float], group_of: dict[str, str],
    max_equity_pct: float, per_sleeve_max_pct: float,
) -> tuple[dict[str, float], dict | None]:
    """Cap the aggregate weight of equity-group sleeves at ``max_equity_pct`` of
    gross, redistributing the freed weight to the NON-equity sleeves (by their
    inverse-vol share) so the book stays at its gross target. Fail-open: if there
    are no non-equity sleeves to diversify into, the cap is skipped (you cannot
    make an all-equity basket cross-asset). Respects ``per_sleeve_max_pct`` on the
    receiving sleeves; any weight that cannot be redistributed lowers gross and is
    reported. Returns (weights, note_or_None)."""
    if not weights or max_equity_pct is None or max_equity_pct <= 0:
        return weights, None
    gross = sum(abs(w) for w in weights.values())
    if gross <= 0:
        return weights, None
    equity = {t: w for t, w in weights.items() if _is_equity_group(group_of.get(t, ""))}
    non_equity = {t: w for t, w in weights.items() if t not in equity}
    equity_total = sum(equity.values())
    cap_abs = max_equity_pct * gross
    if not non_equity or equity_total <= cap_abs + 1e-9:
        # Nothing to cap, or nowhere to put the freed weight (fail-open).
        return weights, None

    out = dict(weights)
    scale = cap_abs / equity_total
    for t in equity:
        out[t] = equity[t] * scale
    freed = equity_total - cap_abs

    # Redistribute `freed` to non-equity sleeves in proportion to their original
    # inverse-vol weight, iterating so a sleeve that hits per_sleeve_max_pct hands
    # its overflow to the others. `placed` accumulates what actually lands.
    orig_non_equity = dict(non_equity)
    sleeve_cap = per_sleeve_max_pct * gross if per_sleeve_max_pct > 0 else gross
    placed = 0.0
    for _ in range(len(non_equity) + 2):
        remaining = freed - placed
        if remaining <= 1e-9:
            break
        base_total = sum(orig_non_equity[t] for t in non_equity if out[t] < sleeve_cap - 1e-9)
        if base_total <= 1e-9:
            break
        round_placed = 0.0
        for t in non_equity:
            room = sleeve_cap - out[t]
            if room <= 1e-9:
                continue
            give = min((orig_non_equity[t] / base_total) * remaining, room)
            if give > 0:
                out[t] += give
                round_placed += give
        placed += round_placed
        if round_placed <= 1e-12:
            break

    note = {
        "equity_before": round(equity_total, 6),
        "equity_cap": round(cap_abs, 6),
        "redistributed": round(placed, 6),
        "residual_ungross": round(freed - placed, 6),
        "max_equity_pct": max_equity_pct,
    }
    return out, note


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

    # P02e review: feasibility diagnostics. Identify binding caps and the
    # gap between requested and achieved exposure so the UI can explain
    # cash drag / underinvestment.
    binding_caps: list[str] = []
    name_cap_eps = 1e-6
    for t, w in weights.items():
        if abs(abs(w) - constraints.max_position_pct) < name_cap_eps:
            binding_caps.append(f"max_position:{t}")
    if sector_notes:
        binding_caps.extend([f"max_sector:{n['sector']}" for n in sector_notes])
    gross_shortfall = max(0.0, gross - gross_pct)
    net_shortfall = abs(net - net_pct)
    underinvested = gross_shortfall > 0.01 * gross  # > 1% of requested gross
    underinvestment_reason = ""
    if underinvested:
        if not actionable:
            underinvestment_reason = "no_actionable_candidates"
        elif binding_caps:
            underinvestment_reason = "binding_caps_after_redistribution"
        else:
            underinvestment_reason = "below_min_position_filter"
    diagnostics = {
        "requested_gross_pct": round(gross, 6),
        "achieved_gross_pct": round(gross_pct, 6),
        "gross_shortfall_pct": round(gross_shortfall, 6),
        "requested_net_pct": round(net, 6),
        "achieved_net_pct": round(net_pct, 6),
        "net_shortfall_pct": round(net_shortfall, 6),
        "binding_caps": binding_caps,
        "underinvested": underinvested,
        "underinvestment_reason": underinvestment_reason,
        "n_actionable_candidates": len(actionable),
        "n_longs_in_book": len(long_w),
        "n_shorts_in_book": len(short_w_pos),
        "n_final_positions": len(weights),
    }

    return ConstructorResult(
        target_weights=weights,
        gross_pct=gross_pct,
        net_pct=net_pct,
        sector_exposure=sector_exposure,
        rejected=rejected,
        diagnostics=diagnostics,
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
    min_positions: int = 3,
    max_positions: int = 15,
    min_aggregate_confidence: float = 0.55,
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
# P02h review: prefer the data-backed ETFHoldingSnapshot table when populated;
# fall back to this hardcoded pair map only when no snapshots exist for either
# ETF in the pair. Pairs map (ETF_A, ETF_B) → estimated holdings overlap.
_OVERLAP_PAIRS: dict[tuple[str, str], float] = {
    ("XLK", "SOXX"): 0.50,
    ("XLK", "XLC"): 0.20,
    ("XLF", "KRE"): 0.45,
    ("XLE", "XOP"): 0.55,
    ("XLV", "IBB"): 0.40,
    ("XLY", "ITB"): 0.30,
}


def _overlap_from_snapshot(a: str, b: str) -> float | None:
    """P02h review: compute overlap as the L1 intersection of holdings
    weights from the most recent ``ETFHoldingSnapshot`` rows.

    Returns None when either ETF has no snapshot rows OR the DB is
    unavailable — caller falls back to the hardcoded pair map.
    """
    try:
        from .models import ETFHoldingSnapshot
    except Exception:
        return None

    def _latest_holdings(etf: str) -> dict[str, float]:
        try:
            latest = (
                ETFHoldingSnapshot.objects.filter(etf_ticker=etf)
                .order_by("-as_of_date")
                .values_list("as_of_date", flat=True)
                .first()
            )
        except Exception:
            return {}
        if latest is None:
            return {}
        try:
            rows = ETFHoldingSnapshot.objects.filter(
                etf_ticker=etf, as_of_date=latest
            ).values_list("constituent_ticker", "weight")
            return {t: float(w) for t, w in rows}
        except Exception:
            return {}

    a_h = _latest_holdings(a)
    b_h = _latest_holdings(b)
    if not a_h or not b_h:
        return None
    # Overlap = sum over constituents of min(weight_a, weight_b).
    common = set(a_h) & set(b_h)
    overlap = sum(min(a_h[t], b_h[t]) for t in common)
    return overlap


def _overlap_fraction(a: str, b: str) -> float:
    snap = _overlap_from_snapshot(a, b)
    if snap is not None:
        return snap
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
class GlobalMacroResult:
    target_weights: dict[str, float]
    gross_pct: float
    net_pct: float
    sector_exposure: dict[str, float]      # by asset_class (re-labelled for UI reuse)
    asset_class_exposure: dict[str, float]
    rejected: list[dict]
    netted_pairs: list[dict]


DEFAULT_ASSET_CLASS_CAPS: dict[str, float] = {
    "equity": 0.50, "rates": 0.50, "commodity": 0.30,
    "fx_proxy": 0.20, "em": 0.20, "inflation": 0.30,
}


def construct_global_macro(
    candidates: list[Candidate],
    *,
    target_gross_pct: float = 1.00,
    per_etf_max_pct: float = 0.30,
    per_etf_min_pct: float = 0.05,
    max_etfs_held: int = 8,
    asset_class_caps: dict[str, float] | None = None,
    asset_class_of: dict[str, str] | None = None,
    inverse_of: dict[str, str] | None = None,
) -> GlobalMacroResult:
    """Long-only macro-ETF allocation.

    candidate.sector is repurposed to carry asset_class; the optional
    `asset_class_of` map overrides per-ticker when supplied. inverse_of
    maps an inverse ETF (e.g. SH) → its underlying (SPY) for netting.

    Steps:
      1. PM action whitelist: only `buy` survives.
      2. Net conflicting long/inverse positions on the same underlying:
         keep the higher-confidence side; log the other.
      3. Per-ETF cap + min-floor + per-position-count ceiling.
      4. Per-asset-class cap with proportional scale-down.
    """
    asset_class_caps = {**DEFAULT_ASSET_CLASS_CAPS, **(asset_class_caps or {})}
    asset_class_of = asset_class_of or {}
    inverse_of = inverse_of or {}

    rejected: list[dict] = []
    survivors: list[Candidate] = []
    for c in candidates:
        if c.action in {"open_short", "cover_short"} or c.side == "short":
            raise PMActionWhitelistError(
                f"global_macro cannot accept {c.action!r} on {c.ticker} "
                f"(inverse ETFs are bought, not shorted)"
            )
        if c.veto_reason:
            rejected.append({"ticker": c.ticker, "reason": c.veto_reason})
            continue
        if c.action != "buy":
            rejected.append({"ticker": c.ticker, "reason": f"action={c.action}"})
            continue
        survivors.append(c)

    # Step 2: Long/inverse netting. underlying → list of (candidate, is_inverse).
    netted_pairs: list[dict] = []
    underlying_groups: dict[str, list[tuple[Candidate, bool]]] = {}
    for c in survivors:
        und = inverse_of.get(c.ticker, "") or c.ticker
        is_inv = bool(inverse_of.get(c.ticker))
        underlying_groups.setdefault(und, []).append((c, is_inv))
    deduped: list[Candidate] = []
    for und, members in underlying_groups.items():
        has_long = any(not inv for _, inv in members)
        has_inv = any(inv for _, inv in members)
        if has_long and has_inv:
            # Conflict: keep the higher-confidence direction.
            best = max(members, key=lambda x: x[0].confidence * x[0].quality_weight)
            for c, _inv in members:
                if c.ticker != best[0].ticker:
                    netted_pairs.append({"dropped": c.ticker, "kept": best[0].ticker,
                                         "underlying": und})
                    rejected.append({"ticker": c.ticker,
                                     "reason": f"netted_against_{best[0].ticker}"})
            deduped.append(best[0])
        else:
            for c, _inv in members:
                deduped.append(c)

    deduped.sort(key=lambda c: c.confidence * c.quality_weight, reverse=True)
    if per_etf_min_pct > 0:
        floor_cap = int(target_gross_pct / per_etf_min_pct)
        max_etfs_held = min(max_etfs_held, max(1, floor_cap))
    deduped = deduped[:max_etfs_held]

    if not deduped:
        return GlobalMacroResult(
            target_weights={}, gross_pct=0.0, net_pct=0.0,
            sector_exposure={}, asset_class_exposure={},
            rejected=rejected, netted_pairs=netted_pairs,
        )

    weights = _bucket_weights(deduped, target_gross_pct)
    weights = _apply_per_name_cap(weights, per_etf_max_pct)

    # Per-asset-class cap: scale down each class proportionally if it breaches.
    ac_of = {c.ticker: (asset_class_of.get(c.ticker) or c.sector or "") for c in deduped}
    by_class: dict[str, float] = {}
    for t, w in weights.items():
        by_class[ac_of.get(t, "")] = by_class.get(ac_of.get(t, ""), 0.0) + abs(w)
    for ac, total in list(by_class.items()):
        cap = asset_class_caps.get(ac)
        if cap is not None and total > cap + 1e-9:
            scale = cap / total
            for t in list(weights.keys()):
                if ac_of.get(t, "") == ac:
                    weights[t] *= scale
            rejected.append({"reason": "asset_class_scaled", "asset_class": ac,
                             "scaled_from": round(total, 4), "to": round(cap, 4)})

    # Floor enforcement.
    if per_etf_min_pct > 0 and weights:
        below = {t: w for t, w in weights.items() if 0 < w < per_etf_min_pct}
        if below:
            extra = sum(per_etf_min_pct - w for w in below.values())
            for t in below:
                weights[t] = per_etf_min_pct
            donors = {t: w for t, w in weights.items()
                      if w > per_etf_min_pct and t not in below}
            donor_total = sum(donors.values())
            if donor_total > extra:
                for t in donors:
                    share = (donors[t] / donor_total) * extra
                    weights[t] = max(per_etf_min_pct, weights[t] - share)

    asset_class_exposure: dict[str, float] = {}
    for t, w in weights.items():
        ac = ac_of.get(t, "")
        asset_class_exposure[ac] = asset_class_exposure.get(ac, 0.0) + w

    gross_pct = sum(abs(w) for w in weights.values())
    net_pct = sum(weights.values())

    return GlobalMacroResult(
        target_weights=weights,
        gross_pct=gross_pct,
        net_pct=net_pct,
        # Reuse sector_exposure JSON for the UI heat-map; here it tracks asset class.
        sector_exposure=dict(asset_class_exposure),
        asset_class_exposure=asset_class_exposure,
        rejected=rejected,
        netted_pairs=netted_pairs,
    )


@dataclass
class RiskParityResult:
    target_weights: dict[str, float]
    gross_pct: float
    net_pct: float
    sector_exposure: dict[str, float]
    rejected: list[dict]
    within_band: bool
    max_drift: float
    diagnostics: dict


def construct_risk_parity(
    sleeves: list[tuple[str, str]],   # [(ticker, sector_or_asset_class), ...]
    vols: dict[str, float],
    *,
    target_gross_pct: float = 1.0,
    per_sleeve_max_pct: float = 0.50,
    per_sleeve_min_pct: float = 0.02,
    excluded: dict[str, str] | None = None,
    current_weights: dict[str, float] | None = None,
    rebalance_band_pct: float = 0.05,
    max_equity_pct: float | None = None,
) -> RiskParityResult:
    """Inverse-volatility weighting (risk parity lite).

    raw_w_i = 1 / σ_i over surviving sleeves; normalise to target_gross_pct;
    apply per-sleeve max and min floors; compute drift vs current_weights to
    decide whether trades are actually needed today.

    ``max_equity_pct`` (P11 D1, None=off): cap aggregate equity-sleeve weight at
    this fraction of gross, redistributing to non-equity sleeves — makes the book
    genuinely cross-asset instead of ~75% equity risk. Needs real sleeve group
    labels (fails open on all-equity or unlabelled sleeves).
    """
    excluded = excluded or {}
    rejected: list[dict] = []
    survivors: list[tuple[str, str]] = []
    for ticker, group in sleeves:
        if ticker in excluded:
            rejected.append({"ticker": ticker, "reason": excluded[ticker]})
            continue
        sigma = vols.get(ticker, 0.0)
        if not sigma or sigma <= 0:
            rejected.append({"ticker": ticker, "reason": "vol_unavailable"})
            continue
        survivors.append((ticker, group))

    if not survivors:
        return RiskParityResult(
            target_weights={}, gross_pct=0.0, net_pct=0.0,
            sector_exposure={}, rejected=rejected,
            within_band=False, max_drift=0.0, diagnostics={},
        )

    raw = {t: 1.0 / vols[t] for t, _g in survivors}
    s = sum(raw.values())
    weights = {t: (r / s) * target_gross_pct for t, r in raw.items()}

    weights = _apply_per_name_cap(weights, per_sleeve_max_pct)

    if per_sleeve_min_pct > 0 and weights:
        below = {t: w for t, w in weights.items() if 0 < w < per_sleeve_min_pct}
        if below:
            extra = sum(per_sleeve_min_pct - w for w in below.values())
            for t in below:
                weights[t] = per_sleeve_min_pct
            donors = {
                t: w for t, w in weights.items()
                if w > per_sleeve_min_pct and t not in below
            }
            donor_total = sum(donors.values())
            if donor_total > extra:
                for t in donors:
                    share = (donors[t] / donor_total) * extra
                    weights[t] = max(per_sleeve_min_pct, weights[t] - share)

    group_of = {t: g for t, g in survivors}

    # P11 D1 — aggregate equity-class cap (cross-asset risk parity). Off (None) ⇒
    # unchanged; needs real group labels (backtest threads them via sleeve_groups).
    equity_cap_note = None
    if max_equity_pct:
        weights, equity_cap_note = _apply_equity_class_cap(
            weights, group_of, max_equity_pct, per_sleeve_max_pct
        )

    sector_exposure: dict[str, float] = {}
    for t, w in weights.items():
        g = group_of.get(t, "")
        sector_exposure[g] = sector_exposure.get(g, 0.0) + w

    gross_pct = sum(abs(w) for w in weights.values())
    net_pct = sum(weights.values())

    # Drift / rebalance band check vs current weights.
    cur = current_weights or {}
    drifts = []
    for t, w in weights.items():
        if w == 0:
            continue
        c = float(cur.get(t, 0.0))
        drifts.append(abs(c - w) / max(abs(w), 1e-9))
    max_drift = max(drifts) if drifts else 1.0
    cold_start = not cur
    within_band = (not cold_start) and (max_drift <= rebalance_band_pct)

    # P02j review: sleeve-level diagnostics — vol, weight, estimated risk
    # contribution. For pure inverse-vol, each sleeve's risk contribution
    # equals the target gross divided by the number of survivors (the
    # whole point of risk parity), but we compute it explicitly so the UI
    # can show "yes, AAPL contributes ~equal risk to TLT".
    sleeves_diag: list[dict] = []
    for t, _g in survivors:
        w = float(weights.get(t, 0.0))
        sigma = float(vols.get(t, 0.0))
        risk_contrib = w * sigma  # marginal risk contribution; ∑ = portfolio σ
        sleeves_diag.append({
            "ticker": t,
            "group": _g,
            "daily_vol": round(sigma, 6),
            "annualised_vol": round(sigma * (252 ** 0.5), 4),
            "target_weight": round(w, 6),
            "current_weight": round(float(cur.get(t, 0.0)), 6),
            "risk_contribution": round(risk_contrib, 6),
        })

    diagnostics = {
        # Persisted "baseline" identity. Bump when the deterministic
        # inverse-vol formula changes so backtest replays can detect drift.
        "baseline_version": "v1",
        "vols": {t: round(vols[t], 6) for t, _g in survivors},
        "annualised_vols": {t: round(vols[t] * (252 ** 0.5), 4) for t, _g in survivors},
        "n_survivors": len(survivors),
        "n_excluded": len(excluded),
        "cold_start": cold_start,
        # P02j review: persist deterministic weights so a council-veto run
        # can be compared against the no-veto baseline downstream.
        "deterministic_weights": {t: round(w, 6) for t, w in weights.items()},
        "sleeves": sleeves_diag,
        "rebalance_band_pct": rebalance_band_pct,
        "rebalance_skip_reason": (
            f"all sleeves within ±{rebalance_band_pct:.0%} band"
            if within_band else ""
        ),
        "equity_class_cap": equity_cap_note,  # P11 D1: None unless the cap bound
    }

    return RiskParityResult(
        target_weights=weights,
        gross_pct=gross_pct,
        net_pct=net_pct,
        sector_exposure=sector_exposure,
        rejected=rejected,
        within_band=within_band,
        max_drift=max_drift,
        diagnostics=diagnostics,
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


# ---------------------------------------------------------------------------
# Deterministic momentum (P7c) — trend (TSMOM) + cross-sectional sector momentum.
# The constructors are thin wrappers over the engine sizers so live ≡ backtest
# (the backtest's run_deterministic_segment calls the SAME tsmom_weights /
# xsec_momentum_weights). Config is built here and shared with the backtest
# serializer. Params are PINNED canonical values (no IS optimizer — that overfit
# the council runs); the operator tunes only leverage (target_gross_pct →
# max_gross), the vol-estimate window, and (sector) the held count. See
# development-plans/phase-07c-deterministic-momentum-strategies.md + ADR 0026.
# ---------------------------------------------------------------------------

MOMENTUM_LOOKBACKS = [63, 126, 252]   # 3 / 6 / 12 months
MOMENTUM_VOL_TARGET_ANNUAL = 0.10     # recommended operating point (research §4.4)


def _momentum_base_config(strategy) -> dict:
    return {
        "vol_target_annual": MOMENTUM_VOL_TARGET_ANNUAL,
        "max_gross": float(strategy.target_gross_pct or 1.0),
        "vol_lookback_days": int(strategy.vol_window_days or 60),
        "momentum_lookbacks": list(MOMENTUM_LOOKBACKS),
        "price_field": "adjusted_close",
    }


def trend_config(strategy) -> dict:
    """Deterministic-trend (TSMOM) sizing config — shared by live cycle + backtest."""
    return {
        **_momentum_base_config(strategy),
        "sizing": "tsmom",
        # P11 C3 — long/short when the strategy opts in; long/flat otherwise.
        "allow_short": bool(getattr(strategy, "allow_short", False)),
    }


def sector_momentum_config(strategy) -> dict:
    """Cross-sectional sector-momentum sizing config — shared by live cycle + backtest."""
    return {
        **_momentum_base_config(strategy),
        "sizing": "xsec_momentum",
        "top_n": int(strategy.max_etfs_held or 5),
    }


def _wrap_momentum_result(
    weights: dict[str, float], members: list[tuple[str, str]],
    current_weights: dict[str, float] | None,
) -> RiskParityResult:
    """Wrap an engine-sizer weight dict into the deterministic result shape the
    autopilot cycle expects. No rebalance band — momentum rebalances fully each
    cycle, matching the backtest (engine passes no current_weights)."""
    group_of = {t: g for t, g in members}
    sector_exposure: dict[str, float] = {}
    for t, w in weights.items():
        g = group_of.get(t, "")
        sector_exposure[g] = sector_exposure.get(g, 0.0) + w
    cur = current_weights or {}
    drifts = [
        abs(float(cur.get(t, 0.0)) - w) / max(abs(w), 1e-9)
        for t, w in weights.items() if w != 0
    ]
    return RiskParityResult(
        target_weights={t: round(w, 6) for t, w in weights.items()},
        gross_pct=sum(abs(w) for w in weights.values()),
        net_pct=sum(weights.values()),
        sector_exposure=sector_exposure,
        rejected=[],
        within_band=False,   # no band: rebalance every cycle (live ≡ backtest)
        max_drift=max(drifts) if drifts else 1.0,
        diagnostics={"sizing": "momentum", "holdings": len(weights)},
    )


def construct_trend(
    day, members: list[tuple[str, str]], *, config: dict,
    current_weights: dict[str, float] | None = None,
) -> RiskParityResult:
    """Deterministic time-series-momentum target weights (long/flat core).
    Wraps engine.tsmom_weights so the live book sizes EXACTLY as the backtest."""
    from apps.backtests.engine import tsmom_weights

    weights = tsmom_weights(day=day, universe=[t for t, _ in members], config=config)
    return _wrap_momentum_result(weights, members, current_weights)


def construct_sector_momentum(
    day, members: list[tuple[str, str]], *, config: dict,
    current_weights: dict[str, float] | None = None,
) -> RiskParityResult:
    """Deterministic cross-sectional momentum target weights (top-N long-only).
    Wraps engine.xsec_momentum_weights so the live book sizes EXACTLY as the backtest."""
    from apps.backtests.engine import xsec_momentum_weights

    weights = xsec_momentum_weights(day=day, universe=[t for t, _ in members], config=config)
    return _wrap_momentum_result(weights, members, current_weights)


# P11 F (R5) — single-name cross-sectional LONG/SHORT (beta-hedged) scaffolding.
# Same wrap-the-engine-sizer pattern as trend/sector so live ≡ backtest. The pod
# is NOT live-deployable until a survivorship-clean single-name backfill exists
# (SCAFFOLDING_KINDS; the §9 gate blocks arming). This is the sizer wiring only.
def xsec_long_short_config(strategy) -> dict:
    """Single-name cross-sectional L/S sizing config — shared by live + backtest."""
    return {
        **_momentum_base_config(strategy),
        "sizing": "xsec_long_short",
        "top_n": int(getattr(strategy, "top_k_longs", 20) or 20),
        "bottom_n": int(getattr(strategy, "top_k_shorts", 20) or 20),
        "beta_benchmark": str(getattr(strategy, "benchmark_ticker", "SPY") or "SPY"),
        "beta_window_days": int(getattr(strategy, "beta_window_days", 252) or 252),
        "beta_hedge": True,
    }


def construct_xsec_long_short(
    day, members: list[tuple[str, str]], *, config: dict,
    current_weights: dict[str, float] | None = None,
) -> RiskParityResult:
    """Deterministic single-name cross-sectional long/short target weights.
    Wraps engine.xsec_long_short_weights so the live book sizes EXACTLY as the
    backtest (top-N long / bottom-N short, beta-hedged)."""
    from apps.backtests.engine import xsec_long_short_weights

    weights = xsec_long_short_weights(day=day, universe=[t for t, _ in members], config=config)
    return _wrap_momentum_result(weights, members, current_weights)
