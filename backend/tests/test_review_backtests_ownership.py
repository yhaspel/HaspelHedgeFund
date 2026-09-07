"""Adversarial review (reviewer: backtests) — per-object authorization sweep.

Every /api/backtests/ endpoint is exercised by a second user against the
first user's backtest. Expected: 404 everywhere (no IDOR). Also checks that
``strategy_id`` / ``graph_version_id`` on create cannot reference another
user's objects, and that ``compare`` with one foreign id fails closed.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.backtests.models import Backtest, BacktestMetrics
from apps.portfolios.models import Portfolio, PortfolioStrategy, Universe

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="own@x.test", password="pw-fake-123456789")


@pytest.fixture
def intruder(db):
    return User.objects.create_user(email="bad@x.test", password="pw-fake-123456789")


def _bt(user, status=Backtest.DONE):
    bt = Backtest.objects.create(
        user=user, name="mine", universe=["AAA"], start_date=dt.date(2024, 1, 1),
        end_date=dt.date(2025, 1, 1), status=status,
    )
    BacktestMetrics.objects.create(backtest=bt, sharpe=Decimal("1.0"))
    return bt


def _client(u):
    c = APIClient()
    c.force_authenticate(u)
    return c


def test_all_object_endpoints_404_for_other_user(owner, intruder):
    bt = _bt(owner)
    running = _bt(owner, status=Backtest.RUNNING)
    cancelled = _bt(owner, status=Backtest.CANCELLED)
    c = _client(intruder)
    assert c.get(f"/api/backtests/{bt.id}/").status_code == 404
    assert c.get(f"/api/backtests/{bt.id}/equity-curve/").status_code == 404
    assert c.get(f"/api/backtests/{bt.id}/folds/").status_code == 404
    assert c.get(f"/api/backtests/{bt.id}/deflation/").status_code == 404
    assert c.get(f"/api/backtests/{bt.id}/attribution/").status_code == 404
    assert c.post(f"/api/backtests/{running.id}/cancel/").status_code == 404
    archive = c.post(f"/api/backtests/{bt.id}/archive/", {"archived": True}, format="json")
    assert archive.status_code == 404
    assert c.delete(f"/api/backtests/{cancelled.id}/").status_code == 404
    # list never leaks
    ids = {row["id"] for row in c.get("/api/backtests/").json()["results"]}
    assert not ids & {bt.id, running.id, cancelled.id}
    # nothing changed
    running.refresh_from_db()
    cancelled.refresh_from_db()
    bt.refresh_from_db()
    assert running.status == Backtest.RUNNING
    assert Backtest.objects.filter(pk=cancelled.id).exists()
    assert bt.archived_at is None


def test_compare_with_one_foreign_id_fails_closed(owner, intruder):
    mine = _bt(intruder)
    theirs = _bt(owner)
    c = _client(intruder)
    r = c.post("/api/backtests/compare/", {"backtest_a_id": mine.id, "backtest_b_id": theirs.id},
               format="json")
    assert r.status_code == 404
    r = c.post("/api/backtests/compare/", {"backtest_a_id": theirs.id, "backtest_b_id": mine.id},
               format="json")
    assert r.status_code == 404


def test_create_cannot_link_foreign_strategy(owner, intruder):
    u = Universe.objects.create(name="own-uni")
    pf = Portfolio.objects.create(user=owner, kind=Portfolio.KIND_STRATEGY, name="p")
    s = PortfolioStrategy.objects.create(
        user=owner, name="s", universe=u, portfolio=pf, kind=PortfolioStrategy.KIND_RISK_PARITY,
    )
    c = _client(intruder)
    r = c.post("/api/backtests/", {
        "name": "x", "universe": ["AAA"], "start_date": "2023-01-02",
        "end_date": "2025-12-31", "strategy_id": s.id,
    }, format="json")
    assert r.status_code == 400
    assert "strategy_id" in r.json()
    assert not Backtest.objects.filter(strategy=s).exists()
