"""Screener agent — feature pass + Python ranking. No LLM in this MVP.

Inputs:
  - Strategy config (top_k_longs, top_k_shorts, screener_weights)
  - Universe membership active on `as_of_date`
Output:
  - ScreenerOutput with long_candidates + short_candidates (each with score,
    features, rationale).
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date

from apps.data.providers.fmp import FmpProvider

from .features import (
    DEFAULT_WEIGHTS,
    ScreenerFeatures,
    compute_features,
    long_score,
    short_score,
)

MAX_UNIVERSE = 2000


class ScreenerAbort(RuntimeError):
    pass


def _rationale(side: str, f: ScreenerFeatures, score: float) -> str:
    if side == "long":
        bits = []
        if f.momentum_3m > 0.05:
            bits.append(f"3-mo momentum {f.momentum_3m:+.1%}")
        if f.earnings_yield > 0.06:
            bits.append(f"earnings yield {f.earnings_yield:.1%}")
        if f.quality_roic > 0.10:
            bits.append(f"ROIC {f.quality_roic:.1%}")
        if not bits:
            bits.append("composite long signal")
        return f"Long candidate ({', '.join(bits)}); score {score:+.2f}"
    bits = []
    if f.drawdown_from_high < -0.10:
        bits.append(f"drawdown {f.drawdown_from_high:+.1%} from 12-mo high")
    if f.momentum_3m < -0.05:
        bits.append(f"3-mo momentum {f.momentum_3m:+.1%}")
    if f.debt_to_equity > 1.5:
        bits.append(f"leverage D/E {f.debt_to_equity:.1f}")
    if f.quality_roic < 0:
        bits.append(f"ROIC {f.quality_roic:.1%}")
    if not bits:
        bits.append("composite short signal")
    return f"Short candidate ({', '.join(bits)}); score {score:+.2f}"


def run_screener(
    *,
    members: list[tuple[str, str]],   # [(ticker, sector), ...] active on as_of
    as_of_date: date,
    top_k_longs: int,
    top_k_shorts: int,
    provider: FmpProvider,
    weights: dict[str, float] | None = None,
    long_only: bool = False,
) -> dict:
    if len(members) > MAX_UNIVERSE:
        raise ScreenerAbort(
            f"Universe size {len(members)} exceeds cost guardrail ({MAX_UNIVERSE}). "
            "Refusing to run before LLM/data spend."
        )
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}

    all_feats: list[ScreenerFeatures] = [
        compute_features(t, s, as_of_date, provider=provider) for t, s in members
    ]
    # A ticker with no usable price history (or a synthetic placeholder row)
    # must not be ranked against real names — it is dropped here and reported.
    feats = [f for f in all_feats if f.available and not f.synthetic]
    excluded = [f.ticker for f in all_feats if not (f.available and not f.synthetic)]

    longs = [(f, long_score(f, weights)) for f in feats]
    longs.sort(key=lambda x: x[1], reverse=True)
    long_candidates = [
        {
            "ticker": f.ticker, "sector": f.sector, "score": round(s, 4),
            "features": asdict(f), "rationale": _rationale("long", f, s),
        }
        for f, s in longs[:top_k_longs] if s > -1e8
    ]

    if long_only or top_k_shorts <= 0:
        short_candidates: list[dict] = []
    else:
        shorts = [(f, short_score(f, weights)) for f in feats]
        shorts.sort(key=lambda x: x[1], reverse=True)
        chosen_long_tickers = {c["ticker"] for c in long_candidates}
        short_candidates = [
            {
                "ticker": f.ticker, "sector": f.sector, "score": round(s, 4),
                "features": asdict(f), "rationale": _rationale("short", f, s),
            }
            for f, s in shorts
            if s > -1e8 and f.ticker not in chosen_long_tickers
        ][:top_k_shorts]

    return {
        "as_of_date": as_of_date.isoformat(),
        "universe_size_evaluated": len(members),
        "universe_size_ranked": len(feats),
        "excluded_no_data_count": len(excluded),
        "excluded_no_data": excluded[:200],
        "long_candidates": long_candidates,
        "short_candidates": short_candidates,
    }
