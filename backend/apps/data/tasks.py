"""Periodic data tasks: macro snapshot + Markov regime pre-warm.

Schedule these via Celery beat (or trigger manually) so the live dashboard
always has today's MacroSnapshot + regime snapshots ready without making a
user wait.
"""
from __future__ import annotations

import datetime as dt
import logging

from celery import shared_task

log = logging.getLogger(__name__)


@shared_task
def prewarm_macro_snapshot(as_of_iso: str | None = None) -> str:
    """Build (or refresh) a MacroSnapshot for the given date (default: today UTC)."""
    from hedgefund_agents.macro.macro_agent import compute_snapshot

    as_of = dt.date.fromisoformat(as_of_iso) if as_of_iso else dt.date.today()
    snap = compute_snapshot(as_of)
    log.info(
        "macro snapshot for %s: growth=%s inflation=%s curve=%s policy=%s",
        as_of, snap.growth_quadrant, snap.inflation_regime,
        snap.yield_curve_state, snap.policy_stance,
    )
    return f"{as_of.isoformat()}:{snap.growth_quadrant}"


@shared_task
def prewarm_regime_snapshots(
    as_of_iso: str | None = None, tickers: list[str] | None = None
) -> dict:
    """Daily pre-warm: fit Markov regime models for the always-modelled universe.

    Failures are logged and surfaced as ``missing`` entries — never crashes
    the task. After fitting we also refresh ``MacroSnapshot.markov_consensus``
    for the same ``as_of`` so the dashboard sees the latest vote.
    """
    from apps.data.models import MacroSnapshot
    from hedgefund_agents.macro.markov_regime import (
        InsufficientHistoryError,
        UndertrainedStateError,
    )
    from hedgefund_agents.macro.regime_persistence import (
        ALWAYS_MODELLED_TICKERS,
        compute_markov_consensus,
        fit_and_persist,
    )

    as_of = dt.date.fromisoformat(as_of_iso) if as_of_iso else dt.date.today()
    universe = list(tickers) if tickers else list(ALWAYS_MODELLED_TICKERS)

    fitted: list[str] = []
    missing: list[dict] = []
    for ticker in universe:
        try:
            fit_and_persist(ticker=ticker, as_of_date=as_of)
            fitted.append(ticker)
        except InsufficientHistoryError as exc:
            log.info("regime prewarm: insufficient history for %s: %s", ticker, exc)
            missing.append({"ticker": ticker, "reason": "insufficient_history"})
        except UndertrainedStateError as exc:
            log.info("regime prewarm: undertrained state for %s: %s", ticker, exc)
            missing.append({"ticker": ticker, "reason": "undertrained_state"})
        except Exception as exc:  # pragma: no cover - provider/transient
            log.exception("regime prewarm: failed to fit %s: %s", ticker, exc)
            missing.append({"ticker": ticker, "reason": "fit_error"})

    consensus = compute_markov_consensus(as_of_date=as_of, tickers=universe)
    if MacroSnapshot.objects.filter(as_of_date=as_of).exists():
        MacroSnapshot.objects.filter(as_of_date=as_of).update(
            markov_consensus=consensus
        )

    return {
        "as_of": as_of.isoformat(),
        "fitted": fitted,
        "missing": missing,
        "consensus_state": consensus.get("consensus_state"),
        "consensus_strength": consensus.get("consensus_strength"),
    }
