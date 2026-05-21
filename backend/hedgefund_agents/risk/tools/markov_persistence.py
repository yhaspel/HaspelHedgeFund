"""Deterministic Risk Manager check based on Markov regime persistence (P2m).

A large swing in bull or bear persistence between consecutive snapshots is
mechanical evidence that the regime is shifting — surfacing it without
asking the LLM keeps the signal honest and free.

Returns a dict the council graph hands to the Risk Manager prompt; the
function never writes anywhere and never refits. When no fresh SPY (or
configured ticker) snapshot exists, it returns ``status='unavailable'`` —
the Risk Manager treats that as "no veto from this tool".
"""
from __future__ import annotations

import datetime as dt
from typing import Any

BULL_DROP_THRESHOLD = 0.30   # |bull_persistence drop| > threshold → flag
BEAR_JUMP_THRESHOLD = 0.30   # |bear_persistence jump| > threshold → flag


def markov_persistence_change(
    ticker: str = "SPY",
    *,
    as_of: dt.date | None = None,
    bull_drop_threshold: float = BULL_DROP_THRESHOLD,
    bear_jump_threshold: float = BEAR_JUMP_THRESHOLD,
) -> dict[str, Any]:
    """Compare the latest Markov snapshot for ``ticker`` to its predecessor.

    A bull-persistence drop > ``bull_drop_threshold`` or a bear-persistence
    jump > ``bear_jump_threshold`` is flagged as a deterministic risk
    signal alongside the LLM-driven assessment.
    """
    from apps.data.models import RegimeSnapshot

    qs = RegimeSnapshot.objects.filter(ticker=ticker.upper())
    if as_of is not None:
        qs = qs.filter(as_of_date__lte=as_of)
    current = qs.order_by("-as_of_date").first()
    if current is None:
        return {
            "ticker": ticker.upper(),
            "status": "unavailable",
            "reason": "no_snapshot",
        }
    prior = (
        RegimeSnapshot.objects.filter(
            ticker=ticker.upper(),
            model_type=current.model_type,
            config_hash=current.config_hash,
            as_of_date__lt=current.as_of_date,
        )
        .order_by("-as_of_date")
        .first()
    )
    if prior is None:
        return {
            "ticker": ticker.upper(),
            "status": "single_snapshot",
            "current_state": current.current_state,
            "bull_persistence": current.bull_persistence,
            "bear_persistence": current.bear_persistence,
            "current_state_persistence": current.current_state_persistence,
        }

    bull_delta = float(current.bull_persistence - prior.bull_persistence)
    bear_delta = float(current.bear_persistence - prior.bear_persistence)
    cur_delta = float(
        current.current_state_persistence - prior.current_state_persistence
    )

    flags: list[str] = []
    if bull_delta < -bull_drop_threshold:
        flags.append(
            f"bull_persistence dropped {bull_delta:+.2f} "
            f"(prior {prior.bull_persistence:.2f} → now {current.bull_persistence:.2f})"
        )
    if bear_delta > bear_jump_threshold:
        flags.append(
            f"bear_persistence jumped {bear_delta:+.2f} "
            f"(prior {prior.bear_persistence:.2f} → now {current.bear_persistence:.2f})"
        )
    state_changed = current.current_state != prior.current_state
    return {
        "ticker": ticker.upper(),
        "status": "ok",
        "current_state": current.current_state,
        "prior_state": prior.current_state,
        "state_changed": state_changed,
        "bull_persistence_delta": round(bull_delta, 4),
        "bear_persistence_delta": round(bear_delta, 4),
        "current_state_persistence_delta": round(cur_delta, 4),
        "current_bull_persistence": current.bull_persistence,
        "current_bear_persistence": current.bear_persistence,
        "current_state_persistence": current.current_state_persistence,
        "flags": flags,
        "risk_flag": bool(flags),
        "as_of_current": current.as_of_date.isoformat(),
        "as_of_prior": prior.as_of_date.isoformat(),
    }
