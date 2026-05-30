"""P3b small gaps: flavor-benchmark IQR, per-ticker notification throttle, and
digest mode when a single fire surfaces many material events."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.utils import timezone

from apps.leaderboard import compute
from apps.leaderboard.models import StrategyScorecard
from apps.models_catalog.presets import PERSONA_AGENTS
from apps.notifications.content import build_digest_content
from apps.notifications.models import NotificationChannel, NotificationEvent
from apps.notifications.services import send_notification
from apps.portfolios.models import Portfolio, PortfolioStrategy, PortfolioTarget, Universe
from apps.runs.models import AgentMessage, Decision, Run
from apps.schedules.models import ScheduledRun, ScheduledRunHistory
from apps.schedules.tasks import execute_scheduled_run
from apps.watchlists.models import Watchlist, WatchlistTicker

User = get_user_model()
PERSONA_LIST = sorted(PERSONA_AGENTS)


@pytest.fixture
def user(db):
    return User.objects.create_user(email="gaps@x.test", password="pw-fake-123456789")


# ---------- flavor-benchmark IQR ----------


def _strategy(user, name, returns):
    universe = Universe.objects.create(name=f"u-{name}", is_active=True)
    pf = Portfolio.objects.create(
        user=user, name=f"b-{name}", kind="strategy", cash_balance=Decimal("100000")
    )
    s = PortfolioStrategy.objects.create(
        user=user, name=name, kind=PortfolioStrategy.KIND_LONG_SHORT,
        universe=universe, portfolio=pf,
    )
    base = dt.date(2026, 1, 5)
    for i, r in enumerate(returns):
        PortfolioTarget.objects.create(
            strategy=s, as_of_date=base + dt.timedelta(days=i),
            status=PortfolioTarget.DONE, target_weights={"AAPL": 0.1},
            marked_snapshot={"since_as_of_pct": r},
        )
    return s


def test_flavor_iqr_populated_with_multiple_strategies(user):
    # Two long/short strategies with different return profiles → the flavor row
    # gets a non-null Sharpe IQR (p25/p75), not just a median.
    _strategy(user, "ls-a", ["1.0", "0.5", "1.5", "0.8"])
    _strategy(user, "ls-b", ["-0.5", "2.0", "-1.0", "1.2"])
    today = timezone.localdate()
    compute.recompute_strategies(today)

    flavor = StrategyScorecard.objects.get(
        strategy__isnull=True, flavor=PortfolioStrategy.KIND_LONG_SHORT,
        window="lifetime", as_of=today,
    )
    assert flavor.n_cycles == 2  # two strategies
    assert flavor.sharpe is not None
    assert flavor.sharpe_p25 is not None and flavor.sharpe_p75 is not None
    assert float(flavor.sharpe_p25) <= float(flavor.sharpe_p75)


def test_flavor_iqr_null_with_single_strategy(user):
    _strategy(user, "ls-solo", ["1.0", "0.5", "1.5"])
    today = timezone.localdate()
    compute.recompute_strategies(today)
    flavor = StrategyScorecard.objects.get(
        strategy__isnull=True, flavor=PortfolioStrategy.KIND_LONG_SHORT,
        window="lifetime", as_of=today,
    )
    assert flavor.sharpe is not None  # median still computed
    assert flavor.sharpe_p25 is None and flavor.sharpe_p75 is None  # IQR needs ≥2


# ---------- per-ticker throttle ----------


def test_per_ticker_cap_throttles_after_limit(user, settings):
    settings.NOTIFICATIONS_MAX_PER_TICKER_PER_DAY = 2
    ch = NotificationChannel.objects.create(
        user=user, kind="email", config={"address": "me@x.test"}
    )
    statuses = [
        send_notification(ch, "s", "b", ticker="AAPL").delivery_status for _ in range(3)
    ]
    assert statuses[:2] == [NotificationEvent.SENT, NotificationEvent.SENT]
    assert statuses[2] == NotificationEvent.THROTTLED
    # A different ticker is unaffected by AAPL's cap.
    assert send_notification(ch, "s", "b", ticker="MSFT").delivery_status == NotificationEvent.SENT


# ---------- digest content + digest mode ----------


def test_build_digest_content_lists_all_tickers():
    sr = ScheduledRun(name="Daily watch")
    results = [
        {"ticker": "AAPL", "run_id": 1, "reasons": ["signal flip"]},
        {"ticker": "MSFT", "run_id": 2, "reasons": ["new material news"]},
    ]
    content = build_digest_content(results, sr)
    assert "2 material updates" in content["subject"]
    assert "AAPL" in content["text"] and "MSFT" in content["text"]
    assert "AAPL" in content["html"] and "MSFT" in content["html"]


def _watchlist(user, tickers):
    wl = Watchlist.objects.create(user=user, name="W", is_default=True)
    for t in tickers:
        WatchlistTicker.objects.create(watchlist=wl, ticker=t)
    return wl


def _fake_execute_run(signals):
    def _fake(run_id):
        run = Run.objects.get(pk=run_id)
        for i, sig in enumerate(signals):
            AgentMessage.objects.create(
                run=run, agent_name=PERSONA_LIST[i],
                parsed_output={"signal": sig, "confidence": 80},
            )
        Decision.objects.create(run=run, ticker=run.tickers[0], action="buy", confidence=80)
        run.status = Run.DONE
        run.total_cost_usd = Decimal("0.10")
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "total_cost_usd", "finished_at"])

    return _fake


def test_digest_mode_collapses_many_events_into_one_message(user, monkeypatch, settings):
    settings.NOTIFICATIONS_DIGEST_THRESHOLD = 5
    tickers = ["AAPL", "MSFT", "NVDA", "AMD", "TSLA", "GOOG"]  # 6 > threshold
    wl = _watchlist(user, tickers)
    ch = NotificationChannel.objects.create(
        user=user, kind="email", config={"address": "me@x.test"}
    )
    sr = ScheduledRun.objects.create(
        user=user, name="daily", watchlist=wl, cron_expression="25 9 * * 1-5",
        notification_channel=ch,
    )
    hist = ScheduledRunHistory.objects.create(scheduled_run=sr, fire_time_utc=timezone.now())
    monkeypatch.setattr("apps.schedules.tasks.execute_run", _fake_execute_run(["bullish"] * 5))

    execute_scheduled_run(sr.id, hist.id)
    hist.refresh_from_db()
    # 6 new unanimous tickers are all material → one digest, not six messages.
    assert hist.materiality_decision["digest"] is True
    assert len(mail.outbox) == 1
    assert hist.notified_count == 6
