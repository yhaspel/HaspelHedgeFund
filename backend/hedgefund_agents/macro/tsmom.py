"""P7 — time-series-momentum (TSMOM) trend score (CTA-lite, Account 3).

A deterministic per-asset trend score over ``get_daily_bars`` close series — a
1/3/12-month return blend — injected into the council's macro node as a regime
input the personas debate (``run_macro``). TSMOM **feeds** the council rather
than acting as a deterministic allocator, so Account 3 stays council-driven and
walk-forward-backtestable with no backtest-engine change (§3/§9).

No LLM; point-in-time safe (``as_of`` clamps look-ahead in the provider).
"""
from __future__ import annotations

from datetime import date, timedelta

# Trading-session lookbacks for the 1 / 3 / 12-month legs.
_LOOKBACKS = {"r1": 21, "r3": 63, "r12": 252}


def tsmom_score(ticker: str, as_of: date, data_provider) -> dict:
    """1/3/12-month trend blend for ``ticker``. Returns a dict:
    ``{score, r1, r3, r12, posture, available}`` where posture ∈ up|down|flat.
    Degrades to a neutral (flat, score 0) reading when bars are missing — never
    raises, so it can't break the council."""
    neutral = {"score": 0.0, "r1": 0.0, "r3": 0.0, "r12": 0.0,
               "posture": "flat", "available": False}
    try:
        start = as_of - timedelta(days=420)
        bars = data_provider.get_daily_bars(ticker, start=start, end=as_of, as_of=as_of) or []
    except Exception:  # noqa: BLE001 — trend is best-effort
        return neutral
    closes = [float(b.close) for b in bars if getattr(b, "close", None)]
    if len(closes) < 22:  # need at least the 1-month leg
        return neutral

    legs: dict[str, float] = {}
    for key, lb in _LOOKBACKS.items():
        if len(closes) > lb and closes[-1 - lb] > 0:
            legs[key] = closes[-1] / closes[-1 - lb] - 1.0
        else:
            legs[key] = 0.0
    score = sum(legs.values()) / max(1, len(legs))
    posture = "up" if score > 0.005 else ("down" if score < -0.005 else "flat")
    return {
        "score": round(score, 4),
        "r1": round(legs["r1"], 4),
        "r3": round(legs["r3"], 4),
        "r12": round(legs["r12"], 4),
        "posture": posture,
        "available": True,
    }
