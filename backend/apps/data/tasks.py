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

from hedgefund.offline import skip_when_offline

log = logging.getLogger(__name__)


@shared_task
def prewarm_macro_snapshot(as_of_iso: str | None = None) -> str:
    """Build (or refresh) a MacroSnapshot for the given date (default: today UTC)."""
    if skip_when_offline("prewarm_macro_snapshot"):
        return "skipped_offline"
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
def ingest_13f_current_quarter() -> str:
    """Tombstone for the deleted SEC bulk 13F ingest (WAVE-3 P2 item 2).

    The EDGAR Form 13F *data-set* path is gone. It was dead end-to-end: SEC
    renamed the archives in 2024 so every run since 404'd; nothing in the
    codebase ever wrote a ``CusipTicker`` row, so holdings landed with
    ``ticker=""`` and the aggregation built zero snapshots; the by-filer view
    had no callers and no UI. Institutional ownership now comes from FMP
    (Ultimate) only — see ``apps.data.providers.ownership.OwnershipResolver``.

    The ``ingest-13f-datasets`` entry is gone from ``hedgefund/celery.py``, but
    beat runs on ``DatabaseScheduler``: removing a schedule from the config does
    NOT delete its ``PeriodicTask`` row, so a deployed instance keeps firing the
    stored entry. Migration ``data.0012`` deletes that row, and this no-op stays
    one release longer so an instance that has not migrated yet logs a skip
    instead of "Received unregistered task". Safe to delete after 0012 has run
    everywhere.
    """
    if skip_when_offline("ingest_13f_current_quarter"):
        return "skipped_offline"
    log.info(
        "ingest_13f_current_quarter: no-op — the SEC bulk 13F ingest was removed "
        "(WAVE-3 P2). If this still fires, migration data.0012 has not run here."
    )
    return "removed"


#: Form types the provenance refresh tops up (matches the news/filings agents).
REFRESH_FORM_TYPES = ["10-K", "10-Q", "8-K"]


@shared_task
def refresh_ticker_data(ticker: str, user_id: int | None = None) -> dict:
    """Top up bars + dividends + cached EDGAR filings for one ticker.

    WAVE-3 P2 item 4: pure delegation to the refresh helpers that already
    exist — this task adds no refresh logic of its own.

      * bars + dividends: ``apps.data.freshness.refresh_universe_bars`` (which
        force-fetches the recent tail, tops the ``CorporateAction`` dividend
        table up from the provider, and re-normalises the adjusted tail);
      * filings: ``EdgarProvider.get_recent_filings``, whose own cache-first
        path refreshes ``FilingRecord`` when the newest cached row is stale.

    Best-effort per leg: a provider outage is reported in the return value, it
    never fails the task.
    """
    if skip_when_offline("refresh_ticker_data"):
        return {"status": "skipped_offline", "ticker": ticker}

    from django.contrib.auth import get_user_model

    from apps.data.freshness import refresh_universe_bars
    from apps.data.providers.factory import get_edgar_provider, get_fmp_provider

    ticker = (ticker or "").upper()
    as_of = dt.date.today()
    out: dict = {"ticker": ticker, "bars": "skipped", "filings": "skipped"}

    user = None
    if user_id is not None:
        user = get_user_model().objects.filter(pk=user_id).first()
    try:
        provider = get_fmp_provider(user=user)
    except RuntimeError as exc:
        out["bars"] = f"no_key: {exc}"
    else:
        try:
            refresh_universe_bars([ticker], as_of, provider)
            out["bars"] = "ok"
        except Exception as exc:  # noqa: BLE001 — one bad ticker must not fail the job
            log.warning("refresh_ticker_data bars failed ticker=%s err=%s", ticker, exc)
            out["bars"] = f"error: {type(exc).__name__}"

    try:
        get_edgar_provider().get_recent_filings(
            ticker, as_of=as_of, form_types=REFRESH_FORM_TYPES, limit=4
        )
        out["filings"] = "ok"
    except Exception as exc:  # noqa: BLE001 — EDGAR outage must not fail the job
        log.warning("refresh_ticker_data filings failed ticker=%s err=%s", ticker, exc)
        out["filings"] = f"error: {type(exc).__name__}"
    return out
