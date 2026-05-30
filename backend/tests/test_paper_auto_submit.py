"""P3b paper auto-submit: Decision → paper BrokerOrder on a scheduled fire, with
the paper-only guard, sizing, daily caps, draft mode, and kill switches."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.brokers.adapters import mock as mock_adapter
from apps.brokers.models import BrokerAccount, BrokerOrder
from apps.portfolios.models import Portfolio
from apps.runs.models import Decision, Run
from apps.schedules.autosubmit import auto_submit_orders
from apps.schedules.models import ScheduledRun, ScheduledRunHistory
from apps.schedules.tasks import execute_scheduled_run
from apps.watchlists.models import Watchlist, WatchlistTicker

User = get_user_model()


@pytest.fixture(autouse=True)
def _reset_mock():
    mock_adapter.reset_state()
    yield
    mock_adapter.reset_state()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="ps@x.test", password="pw-fake-123456789")


def _account(user, *, mode=BrokerAccount.MODE_PAPER, cash="100000", active=True):
    pf = Portfolio.objects.create(
        user=user, name="Broker book", kind=Portfolio.KIND_BROKER, cash_balance=Decimal(cash)
    )
    acc = BrokerAccount.objects.create(
        user=user, broker="mock", mode=mode, account_id=f"demo-{user.id}",
        label="Paper", portfolio=pf,
        connection_status=(
            BrokerAccount.STATUS_ACTIVE if active else BrokerAccount.STATUS_NEEDS_REAUTH
        ),
    )
    mock_adapter.seed_demo_book(acc, cash=Decimal(cash))
    return acc


def _wl(user):
    return Watchlist.objects.create(user=user, name="W", is_default=True)


def _schedule(user, account=None, **kw):
    sr = ScheduledRun.objects.create(
        user=user, name="s", watchlist=_wl(user), cron_expression="25 9 * * 1-5",
        auto_submit_broker_account=account, **kw,
    )
    hist = ScheduledRunHistory.objects.create(scheduled_run=sr, fire_time_utc=timezone.now())
    return sr, hist


def _run(user, ticker, *, action="buy", qty="10", weight="0"):
    run = Run.objects.create(
        user=user, tickers=[ticker], as_of_date=dt.date(2026, 5, 1), status=Run.DONE
    )
    Decision.objects.create(
        run=run, ticker=ticker, action=action, confidence=80,
        target_quantity=Decimal(qty), target_weight_pct=Decimal(weight),
    )
    return run


# ---------- guards ----------


def test_disabled_when_flag_off(user):
    acc = _account(user)
    sr, hist = _schedule(user, acc, auto_paper_submit=False)
    out = auto_submit_orders(sr, hist, [_run(user, "AAPL").id])
    assert out == {"enabled": False}
    assert BrokerOrder.objects.count() == 0


def test_global_kill_switch(user, settings):
    settings.PAPER_AUTO_SUBMIT_ENABLED = False
    acc = _account(user)
    sr, hist = _schedule(user, acc, auto_paper_submit=True)
    out = auto_submit_orders(sr, hist, [_run(user, "AAPL").id])
    assert out["enabled"] is False
    assert BrokerOrder.objects.count() == 0


def test_requires_account(user):
    sr, hist = _schedule(user, None, auto_paper_submit=True)
    out = auto_submit_orders(sr, hist, [_run(user, "AAPL").id])
    assert "no broker account" in out["skipped_all"]
    assert BrokerOrder.objects.count() == 0


def test_live_account_blocked(user):
    acc = _account(user, mode=BrokerAccount.MODE_LIVE)
    sr, hist = _schedule(user, acc, auto_paper_submit=True)
    out = auto_submit_orders(sr, hist, [_run(user, "AAPL").id])
    assert "paper-only" in out["skipped_all"]
    assert BrokerOrder.objects.count() == 0  # never even created for a live account


def test_inactive_account_skipped(user):
    acc = _account(user, active=False)
    sr, hist = _schedule(user, acc, auto_paper_submit=True)
    out = auto_submit_orders(sr, hist, [_run(user, "AAPL").id])
    assert "skipped_all" in out
    assert BrokerOrder.objects.count() == 0


# ---------- submission ----------


def test_fills_paper_order(user):
    acc = _account(user)
    sr, hist = _schedule(
        user, acc, auto_paper_submit=True, max_notional_per_day_usd=Decimal("1000000")
    )
    out = auto_submit_orders(sr, hist, [_run(user, "AAPL", qty="10").id])
    assert out["submitted"] == 1 and out["mode"] == "fill"
    order = BrokerOrder.objects.get()
    assert order.ticker == "AAPL" and order.side == "buy" and order.quantity == Decimal("10")
    assert order.confirmation_method == BrokerOrder.CONFIRM_SCHEDULED
    assert order.status in (
        BrokerOrder.STATUS_SUBMITTED, BrokerOrder.STATUS_FILLED, BrokerOrder.STATUS_PARTIAL,
    )
    assert hist.broker_orders.count() == 1


def test_draft_only_leaves_order_unsubmitted(user):
    acc = _account(user)
    sr, hist = _schedule(
        user, acc, auto_paper_submit=True, auto_submit_draft_only=True,
        max_notional_per_day_usd=Decimal("1000000"),
    )
    out = auto_submit_orders(sr, hist, [_run(user, "AAPL").id])
    assert out["submitted"] == 1 and out["mode"] == "draft"
    order = BrokerOrder.objects.get()
    assert order.status == BrokerOrder.STATUS_DRAFT
    assert order.idempotency_state == BrokerOrder.IDEM_UNSUBMITTED


def test_hold_is_skipped(user):
    acc = _account(user)
    sr, hist = _schedule(user, acc, auto_paper_submit=True)
    out = auto_submit_orders(sr, hist, [_run(user, "AAPL", action="hold").id])
    assert out["submitted"] == 0
    assert any("action=hold" in i.get("skipped", "") for i in out["items"])
    assert BrokerOrder.objects.count() == 0


def test_sizing_falls_back_to_weight_times_nav(user):
    acc = _account(user, cash="100000")
    sr, hist = _schedule(
        user, acc, auto_paper_submit=True, max_notional_per_day_usd=Decimal("1000000")
    )
    # No target_quantity, 10% weight, $100k NAV, $100 fallback price → 100 shares.
    out = auto_submit_orders(sr, hist, [_run(user, "AAPL", qty="0", weight="10").id])
    assert out["submitted"] == 1
    assert BrokerOrder.objects.get().quantity == Decimal("100")


# ---------- caps ----------


def test_daily_order_cap(user):
    acc = _account(user)
    sr, hist = _schedule(
        user, acc, auto_paper_submit=True, max_orders_per_day=1,
        max_notional_per_day_usd=Decimal("1000000"),
    )
    runs = [_run(user, "AAPL").id, _run(user, "MSFT").id]
    out = auto_submit_orders(sr, hist, runs)
    assert out["submitted"] == 1
    assert any("order cap" in i.get("skipped", "") for i in out["items"])
    assert BrokerOrder.objects.count() == 1


def test_daily_notional_cap(user):
    acc = _account(user)
    # 10 shares × $100 fallback = $1,000; cap at $500 → skipped.
    sr, hist = _schedule(
        user, acc, auto_paper_submit=True, max_notional_per_day_usd=Decimal("500")
    )
    out = auto_submit_orders(sr, hist, [_run(user, "AAPL", qty="10").id])
    assert out["submitted"] == 0
    assert any("notional cap" in i.get("skipped", "") for i in out["items"])


# ---------- e2e hook ----------


def test_execute_scheduled_run_wires_submit_decision(user, monkeypatch):
    acc = _account(user)
    wl = Watchlist.objects.create(user=user, name="WL", is_default=True)
    WatchlistTicker.objects.create(watchlist=wl, ticker="AAPL")
    sr = ScheduledRun.objects.create(
        user=user, name="daily", watchlist=wl, cron_expression="25 9 * * 1-5",
        auto_paper_submit=True, auto_submit_broker_account=acc,
        max_notional_per_day_usd=Decimal("1000000"),
    )
    hist = ScheduledRunHistory.objects.create(scheduled_run=sr, fire_time_utc=timezone.now())

    def _fake(run_id):
        run = Run.objects.get(pk=run_id)
        Decision.objects.create(
            run=run, ticker=run.tickers[0], action="buy", confidence=80,
            target_quantity=Decimal("5"),
        )
        run.status = Run.DONE
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at"])

    monkeypatch.setattr("apps.schedules.tasks.execute_run", _fake)
    execute_scheduled_run(sr.id, hist.id)
    hist.refresh_from_db()
    assert hist.submit_decision.get("submitted") == 1
    assert hist.broker_orders.count() == 1


# ---------- API / serializer validation ----------


def _client(user):
    from rest_framework.test import APIClient

    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _payload(wl, **kw):
    body = {
        "name": "s", "watchlist": wl.id, "cron_expression": "25 9 * * 1-5",
        "model_preset": "frugal",
    }
    body.update(kw)
    return body


def test_api_rejects_auto_submit_without_account(user):
    resp = _client(user).post(
        "/api/scheduled-runs/", _payload(_wl(user), auto_paper_submit=True), format="json"
    )
    assert resp.status_code == 400
    assert "auto_submit_broker_account" in resp.data


def test_api_rejects_live_account(user):
    acc = _account(user, mode=BrokerAccount.MODE_LIVE)
    resp = _client(user).post(
        "/api/scheduled-runs/",
        _payload(_wl(user), auto_paper_submit=True, auto_submit_broker_account=acc.id),
        format="json",
    )
    assert resp.status_code == 400
    assert "auto_submit_broker_account" in resp.data


def test_api_creates_with_paper_account(user):
    acc = _account(user)
    resp = _client(user).post(
        "/api/scheduled-runs/",
        _payload(
            _wl(user), auto_paper_submit=True, auto_submit_broker_account=acc.id,
            max_orders_per_day=5,
        ),
        format="json",
    )
    assert resp.status_code == 201
    sr = ScheduledRun.objects.get(pk=resp.data["id"])
    assert sr.auto_paper_submit is True
    assert sr.auto_submit_broker_account_id == acc.id
    assert sr.max_orders_per_day == 5
