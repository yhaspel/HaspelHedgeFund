"""Periodic data tasks: macro snapshot pre-warm.

The macro snapshot beat task uses the FRED public-data provider only, which is
policy-exempt from the BYOK-only rule under data-licensing.md. There is no
platform-key path for any paid provider (FMP, Tiingo) — those go through
user-keyed factories on user-triggered code paths.
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


def _most_recent_completed_quarter(today: dt.date | None = None) -> str:
    """Return the most-recently-filed 13F quarter (e.g. ``2024Q4``).

    13F is due ~45 days after quarter-end, so when the beat runs (Feb/May/
    Aug/Nov) the quarter that just became fully available is the *prior*
    calendar quarter.
    """
    today = today or dt.date.today()
    q = (today.month - 1) // 3 - 1  # prior quarter index (0..3)
    year = today.year
    if q < 0:
        q = 3
        year -= 1
    return f"{year}Q{q + 1}"


@shared_task
def ingest_13f_current_quarter() -> str:
    """Ingest the most-recently-completed 13F quarter's SEC data set."""
    from django.core.management import call_command

    quarter = _most_recent_completed_quarter()
    call_command("ingest_13f_datasets", "--quarter", quarter)
    return quarter
