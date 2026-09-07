"""Review (feresearch) — what the Schedules page shows vs what the dispatcher does.

1. ``next_run_at`` (the "Next run" column) is the raw cron-next and lands on
   NYSE holidays for a market-aware schedule. FIXED: when that fire time comes
   the dispatcher still skips it, but now writes the ``ScheduledRunHistory``
   row the guide (schedules.md §History) promises — ``status="skipped"`` with
   ``market closed (holiday)`` — and advances to the next trading day.

2. The UI labels the ceiling "Cost ceiling (USD / run)" while the dispatcher
   compares it against ``est_total_usd`` = per-run × n_tickers, i.e. the whole
   fire. A 5-ticker watchlist with a $0.30/run ceiling and a $0.10/run estimate
   is skipped even though every run is under the "per run" ceiling.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model

from apps.schedules.models import ScheduledRun, ScheduledRunHistory
from apps.schedules.tasks import dispatch_due_scheduled_runs, execute_scheduled_run
from apps.schedules.triggers import compute_next, market_gate_ok
from apps.watchlists.models import Watchlist, WatchlistTicker

User = get_user_model()
UTC = ZoneInfo("UTC")
NY = ZoneInfo("America/New_York")


@pytest.fixture
def user(db):
    return User.objects.create_user(email="sched-ui@x.test", password="pw-fake-123456789")


def _watchlist(user, tickers):
    wl = Watchlist.objects.create(user=user, name="W", is_default=True)
    for t in tickers:
        WatchlistTicker.objects.create(watchlist=wl, ticker=t)
    return wl


def test_next_run_display_lands_on_a_holiday_and_the_skip_leaves_history(user, monkeypatch):
    wl = _watchlist(user, ["AAPL"])
    sr = ScheduledRun.objects.create(
        user=user, name="s", watchlist=wl, cron_expression="25 9 * * 1-5",
        is_market_aware=True,
    )
    # What the UI would show as "Next run" the evening before Thanksgiving 2026.
    wed_evening = dt.datetime(2026, 11, 25, 18, 0, tzinfo=NY).astimezone(UTC)
    nxt = compute_next(sr.cron_expression, sr.timezone, after=wed_evening)
    assert nxt.astimezone(NY).date() == dt.date(2026, 11, 26)  # Thanksgiving
    assert market_gate_ok(sr, nxt) is False  # …which will be skipped

    # Fire time arrives.
    sr.next_run_at = nxt
    sr.save(update_fields=["next_run_at"])
    frozen_now = nxt + dt.timedelta(seconds=30)
    monkeypatch.setattr("apps.schedules.tasks.timezone.now", lambda: frozen_now)
    calls: list = []
    monkeypatch.setattr(
        "apps.schedules.tasks.execute_scheduled_run.delay", lambda *a, **k: calls.append(a)
    )
    out = dispatch_due_scheduled_runs()
    assert out == {"due": 1, "dispatched": 0, "skipped_market": 1}
    assert calls == []
    # The History table the user can open now explains the missing run.
    rows = list(ScheduledRunHistory.objects.filter(scheduled_run=sr))
    assert len(rows) == 1
    assert rows[0].fire_time_utc == nxt
    assert rows[0].status == ScheduledRunHistory.SKIPPED
    assert rows[0].error == "market closed (holiday)"
    assert rows[0].finished_at is not None
    sr.refresh_from_db()
    assert sr.next_run_at.astimezone(NY).date() == dt.date(2026, 11, 27)


def test_ceiling_labelled_per_run_is_enforced_per_fire(user, monkeypatch):
    wl = _watchlist(user, ["AAPL", "MSFT", "NVDA", "AMZN", "GOOG"])
    sr = ScheduledRun.objects.create(
        user=user, name="s", watchlist=wl, cron_expression="25 9 * * 1-5",
        is_market_aware=False, cost_ceiling_usd=Decimal("0.30"), on_breach=ScheduledRun.SKIP,
    )
    hist = ScheduledRunHistory.objects.create(
        scheduled_run=sr, fire_time_utc=dt.datetime.now(tz=UTC)
    )
    # $0.10 per council run — under the "USD / run" ceiling of $0.30.
    monkeypatch.setattr(
        "apps.schedules.tasks.estimate_run_cost",
        lambda user, preset, overrides, personas, n: {
            "overrides": {}, "per_run_usd": 0.10, "est_total_usd": round(0.10 * n, 4),
        },
    )
    executed: list = []
    monkeypatch.setattr("apps.schedules.tasks.execute_run", lambda run_id: executed.append(run_id))

    out = execute_scheduled_run(sr.id, hist.id)

    hist.refresh_from_db()
    assert out == {"status": "skipped_cost"}
    assert hist.status == ScheduledRunHistory.SKIPPED
    assert hist.error == "cost ceiling exceeded: est $0.5 > $0.3"
    assert executed == []
