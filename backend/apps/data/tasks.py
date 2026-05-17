"""Periodic data tasks: macro snapshot pre-warm.

Schedule this via Celery beat (or trigger manually) so the live dashboard
always has today's MacroSnapshot ready without making a user wait.
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
