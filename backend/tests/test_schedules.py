"""Scheduler: cron/DST, market gating, cost ceiling, dispatch idempotency,
and end-to-end execution (P3b)."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.models_catalog.presets import PERSONA_AGENTS
from apps.notifications.models import NotificationChannel
from apps.runs.models import AgentMessage, Decision, Run
from apps.schedules.costs import cheaper_preset, estimate_run_cost
from apps.schedules.models import ScheduledRun, ScheduledRunHistory
from apps.schedules.tasks import dispatch_due_scheduled_runs, execute_scheduled_run
from apps.schedules.triggers import compute_next, market_gate_ok
from apps.watchlists.models import Watchlist, WatchlistTicker

User = get_user_model()
UTC = ZoneInfo("UTC")
NY = ZoneInfo("America/New_York")
PERSONA_LIST = sorted(PERSONA_AGENTS)


@pytest.fixture
def user(db):
    return User.objects.create_user(email="sched@x.test", password="pw-fake-123456789")


def _watchlist(user, tickers):
    wl = Watchlist.objects.create(user=user, name="W", is_default=True)
    for t in tickers:
        WatchlistTicker.objects.create(watchlist=wl, ticker=t)
    return wl


def _fake_execute_run(signals, *, confidence=80, veto=False, cost="0.10"):
    def _fake(run_id):
        run = Run.objects.get(pk=run_id)
        for i, sig in enumerate(signals):
            AgentMessage.objects.create(
                run=run, agent_name=PERSONA_LIST[i],
                parsed_output={"signal": sig, "confidence": confidence},
            )
        Decision.objects.create(
            run=run, ticker=run.tickers[0], action="buy",
            confidence=confidence, risk_overrides={"veto": veto},
        )
        run.status = Run.DONE
        run.total_cost_usd = Decimal(cost)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "total_cost_usd", "finished_at"])

    return _fake


# ---------- cron + DST ----------


def test_compute_next_skips_weekend():
    # Fri 2026-05-29 10:00 ET → next "25 9 * * 1-5" is Mon 2026-06-01 09:25 ET.
    after = dt.datetime(2026, 5, 29, 10, 0, tzinfo=NY).astimezone(UTC)
    nxt = compute_next("25 9 * * 1-5", "America/New_York", after)
    ny = nxt.astimezone(NY)
    assert (ny.hour, ny.minute) == (9, 25)
    assert ny.weekday() == 0  # Monday


def test_compute_next_dst_offsets():
    # 9:25 ET is 13:25 UTC in summer (EDT, -4) and 14:25 UTC in winter (EST, -5).
    summer = compute_next(
        "25 9 * * *", "America/New_York",
        dt.datetime(2026, 7, 1, 0, 0, tzinfo=NY).astimezone(UTC),
    )
    winter = compute_next(
        "25 9 * * *", "America/New_York",
        dt.datetime(2026, 1, 5, 0, 0, tzinfo=NY).astimezone(UTC),
    )
    assert summer.astimezone(UTC).hour == 13
    assert winter.astimezone(UTC).hour == 14


# ---------- market gate ----------


def test_market_gate_skips_nyse_holiday():
    sr = ScheduledRun(is_market_aware=True)
    thanksgiving = dt.datetime(2026, 11, 26, 9, 25, tzinfo=NY).astimezone(UTC)
    next_day = dt.datetime(2026, 11, 27, 9, 25, tzinfo=NY).astimezone(UTC)
    assert market_gate_ok(sr, thanksgiving) is False
    assert market_gate_ok(sr, next_day) is True


def test_market_gate_disabled_always_fires():
    sr = ScheduledRun(is_market_aware=False)
    thanksgiving = dt.datetime(2026, 11, 26, 9, 25, tzinfo=NY).astimezone(UTC)
    assert market_gate_ok(sr, thanksgiving) is True


# ---------- costs ----------


def test_cheaper_preset_chain():
    assert cheaper_preset("hybrid") == "frugal"
    assert cheaper_preset("frugal") == "dev"
    assert cheaper_preset("dev") is None
    assert cheaper_preset("custom-thing") == "frugal"


def test_estimate_zero_without_prices(user):
    est = estimate_run_cost(user, "dev", {}, [], 3)
    assert est["est_total_usd"] == 0.0
    assert isinstance(est["overrides"], dict) and est["overrides"]


# ---------- dispatcher idempotency ----------


def test_history_unique_per_fire(user):
    wl = _watchlist(user, ["AAPL"])
    sr = ScheduledRun.objects.create(
        user=user, name="s", watchlist=wl, cron_expression="25 9 * * 1-5"
    )
    t = timezone.now()
    ScheduledRunHistory.objects.create(scheduled_run=sr, fire_time_utc=t)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ScheduledRunHistory.objects.create(scheduled_run=sr, fire_time_utc=t)


def test_dispatcher_advances_and_does_not_refire(user, monkeypatch):
    wl = _watchlist(user, ["AAPL"])
    sr = ScheduledRun.objects.create(
        user=user, name="s", watchlist=wl, cron_expression="25 9 * * 1-5",
        is_market_aware=False,
    )
    sr.next_run_at = timezone.now() - dt.timedelta(minutes=1)
    sr.save(update_fields=["next_run_at"])

    calls = []
    monkeypatch.setattr(
        "apps.schedules.tasks.execute_scheduled_run.delay",
        lambda *a, **k: calls.append(a),
    )
    out1 = dispatch_due_scheduled_runs()
    out2 = dispatch_due_scheduled_runs()
    assert out1["dispatched"] == 1
    assert out2["dispatched"] == 0  # next_run_at advanced into the future
    assert ScheduledRunHistory.objects.filter(scheduled_run=sr).count() == 1
    assert len(calls) == 1


# ---------- end-to-end execution ----------


def test_execute_fans_out_and_notifies(user, monkeypatch):
    wl = _watchlist(user, ["AAPL", "MSFT", "NVDA"])
    ch = NotificationChannel.objects.create(
        user=user, kind="email", config={"address": "me@x.test"}
    )
    sr = ScheduledRun.objects.create(
        user=user, name="daily", watchlist=wl, cron_expression="25 9 * * 1-5",
        model_preset="hybrid", notification_channel=ch,
    )
    hist = ScheduledRunHistory.objects.create(
        scheduled_run=sr, fire_time_utc=timezone.now()
    )
    monkeypatch.setattr(
        "apps.schedules.tasks.execute_run", _fake_execute_run(["bullish"] * 5)
    )
    out = execute_scheduled_run(sr.id, hist.id)
    assert out["tickers"] == 3
    hist.refresh_from_db()
    assert hist.status == "done"
    assert hist.runs.count() == 3
    # 3 new tickers, all unanimous bullish → materiality fires for each.
    assert hist.notified_count == 3
    assert len(mail.outbox) == 3
    assert hist.actual_cost_usd == Decimal("0.3000")


def test_cost_ceiling_skip(user, monkeypatch):
    wl = _watchlist(user, ["AAPL"])
    sr = ScheduledRun.objects.create(
        user=user, name="s", watchlist=wl, cron_expression="25 9 * * 1-5",
        cost_ceiling_usd=Decimal("0.01"), on_breach=ScheduledRun.SKIP,
    )
    hist = ScheduledRunHistory.objects.create(scheduled_run=sr, fire_time_utc=timezone.now())
    monkeypatch.setattr(
        "apps.schedules.tasks.estimate_run_cost",
        lambda *a, **k: {"est_total_usd": 5.0, "overrides": {}},
    )
    out = execute_scheduled_run(sr.id, hist.id)
    assert out["status"] == "skipped_cost"
    hist.refresh_from_db()
    assert hist.status == "skipped"
    assert hist.runs.count() == 0
    assert Run.objects.filter(user=user).count() == 0


def test_cost_ceiling_degrade(user, monkeypatch):
    wl = _watchlist(user, ["AAPL"])
    sr = ScheduledRun.objects.create(
        user=user, name="s", watchlist=wl, cron_expression="25 9 * * 1-5",
        model_preset="hybrid", cost_ceiling_usd=Decimal("1.00"),
        on_breach=ScheduledRun.DEGRADE,
    )
    hist = ScheduledRunHistory.objects.create(scheduled_run=sr, fire_time_utc=timezone.now())

    costs = {"quality": 10.0, "research": 8.0, "hybrid": 5.0, "frugal": 0.5, "dev": 0.0}
    monkeypatch.setattr(
        "apps.schedules.tasks.estimate_run_cost",
        lambda u, preset, o, p, n: {"est_total_usd": costs.get(preset, 5.0), "overrides": {}},
    )
    monkeypatch.setattr("apps.schedules.tasks.execute_run", _fake_execute_run(["bullish"] * 5))
    out = execute_scheduled_run(sr.id, hist.id)
    assert out["status"] == "done"
    hist.refresh_from_db()
    assert hist.degraded_preset == "hybrid"  # degraded away from hybrid
    assert hist.runs.count() == 1


# ---------- human-readable cron ----------


def test_describe_cron_human_readable():
    # cron-descriptor's exact phrasing varies by version (12h "09:25 AM" vs 24h
    # "09:25"), so assert the stable, meaningful parts rather than the literal.
    from apps.schedules.triggers import describe_cron

    weekday = describe_cron("25 9 * * 1-5")
    assert "09:25" in weekday and "Monday through Friday" in weekday
    assert "Sunday" in describe_cron("0 17 * * 0")
    assert describe_cron("nonsense-not-a-cron") == "nonsense-not-a-cron"  # falls back


def test_serializer_exposes_cron_description(user):
    from apps.schedules.serializers import ScheduledRunSerializer

    wl = _watchlist(user, ["AAPL"])
    sr = ScheduledRun.objects.create(
        user=user, name="s", watchlist=wl, cron_expression="25 9 * * 1-5",
    )
    desc = ScheduledRunSerializer(sr).data["cron_description"]
    assert "09:25" in desc and "Monday through Friday" in desc
