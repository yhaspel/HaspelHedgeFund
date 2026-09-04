"""P10 §D — simplification: strategy archive semantics, runs/backtests
pagination + windows, backtest soft-archive, the assisted archive sweep."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.utils import timezone
from rest_framework.test import APIClient

from apps.backtests.models import Backtest
from apps.brokers.models import BrokerAccount, StrategyBrokerLink
from apps.portfolios import sleeves
from apps.portfolios.models import (
    AutonomousFund,
    Portfolio,
    PortfolioStrategy,
    StrategyAutopilot,
    Universe,
    UniverseMembership,
)
from apps.runs.models import Run

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="p10d@x.test", password="pw-fake-123456789")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _strategy(user, name="S", active=True):
    u = Universe.objects.create(name=f"p10d-uni-{name}")
    UniverseMembership.objects.create(
        universe=u, ticker="AAA", effective_from=dt.date(2024, 1, 1),
    )
    pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_STRATEGY, name=name)
    return PortfolioStrategy.objects.create(
        user=user, name=name, universe=u, portfolio=pf,
        kind=PortfolioStrategy.KIND_LONG_SHORT, is_active=active,
    )


# ---------------------------------------------------------------------------
# D1 — strategy archive semantics.
# ---------------------------------------------------------------------------
def test_strategy_list_hides_archived_by_default(db, client, user):
    live = _strategy(user, "live", active=True)
    dead = _strategy(user, "dead", active=False)
    r = client.get("/api/strategies/")
    ids = [s["id"] for s in r.json()]
    assert live.id in ids and dead.id not in ids
    r2 = client.get("/api/strategies/?include_archived=1")
    ids2 = [s["id"] for s in r2.json()]
    assert live.id in ids2 and dead.id in ids2


def test_strategy_archive_via_patch(db, client, user):
    s = _strategy(user, "to-archive")
    r = client.patch(f"/api/strategies/{s.id}/", {"is_active": False}, format="json")
    assert r.status_code == 200
    s.refresh_from_db()
    assert s.is_active is False
    # Unarchive round-trips.
    client.patch(f"/api/strategies/{s.id}/", {"is_active": True}, format="json")
    s.refresh_from_db()
    assert s.is_active is True


def test_leaderboard_recompute_skips_archived(db, user):
    from apps.leaderboard.compute import recompute_strategies
    from apps.leaderboard.models import StrategyScorecard

    live = _strategy(user, "lb-live", active=True)
    dead = _strategy(user, "lb-dead", active=False)
    recompute_strategies(timezone.localdate())
    strategies_with_cards = set(
        StrategyScorecard.objects.filter(strategy__isnull=False)
        .values_list("strategy_id", flat=True)
    )
    assert live.id in strategies_with_cards
    assert dead.id not in strategies_with_cards


# ---------------------------------------------------------------------------
# D2 — the assisted sweep command (the live-id sweep itself is a CLI step).
# ---------------------------------------------------------------------------
def test_archive_sweep_archives_orphans(db, user):
    a = _strategy(user, "orphan-a")
    b = _strategy(user, "orphan-b")
    out = StringIO()
    call_command("archive_strategies", ids=[a.id, b.id], stdout=out)
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.is_active is False and b.is_active is False
    call_command("archive_strategies", ids=[a.id], unarchive=True, stdout=StringIO())
    a.refresh_from_db()
    assert a.is_active is True


def test_archive_sweep_refuses_fund_members_and_enabled_autopilots(db, user):
    member = _strategy(user, "fund-member")
    fund = AutonomousFund.objects.create(owner=user, name="Fund")
    sleeves.create_sleeve(fund, member, 100)
    with pytest.raises(CommandError, match="fund member"):
        call_command("archive_strategies", ids=[member.id], stdout=StringIO())

    hot = _strategy(user, "hot")
    pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_BROKER, name="bk")
    acc = BrokerAccount.objects.create(
        user=user, broker="mock", mode=BrokerAccount.MODE_PAPER,
        account_id="mock-x", label="X", portfolio=pf,
        connection_status=BrokerAccount.STATUS_ACTIVE,
    )
    StrategyBrokerLink.objects.create(strategy=hot, broker_account=acc)
    StrategyAutopilot.objects.create(strategy=hot, broker_account=acc, is_enabled=True)
    with pytest.raises(CommandError, match="broker link|autopilot"):
        call_command("archive_strategies", ids=[hot.id], stdout=StringIO())
    hot.refresh_from_db()
    assert hot.is_active is True


# ---------------------------------------------------------------------------
# D4 — runs pagination + default 30-day window.
# ---------------------------------------------------------------------------
def test_runs_list_paginated_and_windowed(db, client, user):
    old = Run.objects.create(
        user=user, tickers=["AAA"], as_of_date=dt.date(2024, 1, 1), status=Run.DONE,
    )
    Run.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - dt.timedelta(days=90),
    )
    fresh = Run.objects.create(
        user=user, tickers=["BBB"], as_of_date=dt.date(2026, 6, 1), status=Run.DONE,
    )
    body = client.get("/api/runs/").json()
    assert set(body.keys()) >= {"count", "results"}
    ids = [r["id"] for r in body["results"]]
    assert fresh.id in ids and old.id not in ids       # default last-30-days
    body_all = client.get("/api/runs/?days=all").json()
    ids_all = [r["id"] for r in body_all["results"]]
    assert old.id in ids_all and fresh.id in ids_all


def test_runs_page_size_capped(db, client, user):
    for i in range(55):
        Run.objects.create(
            user=user, tickers=[f"T{i}"], as_of_date=dt.date(2026, 6, 1),
            status=Run.DONE,
        )
    body = client.get("/api/runs/").json()
    assert body["count"] == 55
    assert len(body["results"]) == 50
    body2 = client.get("/api/runs/?page=2").json()
    assert len(body2["results"]) == 5


# ---------------------------------------------------------------------------
# D4 — backtest soft archive.
# ---------------------------------------------------------------------------
def _backtest(user, status=Backtest.DONE):
    return Backtest.objects.create(
        user=user, name="bt", universe=["AAA"],
        start_date=dt.date(2024, 1, 1), end_date=dt.date(2025, 1, 1), status=status,
    )


def test_backtest_archive_roundtrip_and_list_filter(db, client, user):
    bt = _backtest(user)
    r = client.post(f"/api/backtests/{bt.id}/archive/", {"archived": True}, format="json")
    assert r.status_code == 200
    bt.refresh_from_db()
    assert bt.archived_at is not None
    body = client.get("/api/backtests/").json()
    assert bt.id not in [b["id"] for b in body["results"]]
    body_all = client.get("/api/backtests/?include_archived=1").json()
    assert bt.id in [b["id"] for b in body_all["results"]]
    # Unarchive restores it.
    client.post(f"/api/backtests/{bt.id}/archive/", {"archived": False}, format="json")
    bt.refresh_from_db()
    assert bt.archived_at is None


def test_backtest_archive_refuses_active(db, client, user):
    bt = _backtest(user, status=Backtest.RUNNING)
    r = client.post(f"/api/backtests/{bt.id}/archive/", {"archived": True}, format="json")
    assert r.status_code == 409


def test_backtest_archive_does_not_change_gate_evidence(db, client, user):
    """Archiving is cosmetic — a strategy's archived record of record still
    satisfies the §9 gate (status/era are the evidential filters)."""
    from apps.backtests.models import BacktestMetrics
    from apps.portfolios.validation import validation_status

    s = _strategy(user, "gate-archived")
    bt = Backtest.objects.create(
        user=user, strategy=s, name="bt", universe=["AAA"],
        start_date=dt.date(2024, 1, 1), end_date=dt.date(2025, 1, 1),
        status=Backtest.DONE, archived_at=timezone.now(),
    )
    BacktestMetrics.objects.create(
        backtest=bt, mean_oos_sharpe=Decimal("0.9"), sharpe=Decimal("0.7"),
        max_drawdown_pct=Decimal("4.0"),
    )
    assert validation_status(s)["passed"] is True
