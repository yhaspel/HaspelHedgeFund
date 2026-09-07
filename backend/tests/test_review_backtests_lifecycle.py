"""Adversarial review (reviewer: backtests) — Celery task lifecycle proofs.

FIXED by WP B3a §4. Each test now asserts the corrected behaviour.

  * L1: the orphan sweeper only consulted ``inspect.active()``. A backtest
        legitimately QUEUED behind long tasks (prefork prefetch => the task id
        sits in ``reserved()``, not ``active()``) for > 15 min was marked FAILED
        — and then still executed when a slot freed up. The sweeper now also
        consults ``reserved()``/``scheduled()`` and skips rows with a fresh
        ``heartbeat_at``.
  * L2: ``run_walkforward`` never checked the current status: a CANCELLED (or
        sweeper-FAILED) backtest whose task ran anyway was flipped
        RUNNING -> DONE, overwriting the terminal state and becoming §9-gate
        eligible. It now claims QUEUED -> RUNNING with a conditional UPDATE and
        raises BacktestNotClaimable when the row is not QUEUED.
  * L3: a QUEUED backtest can still be cancelled before ``celery_task_id`` is
        stored (perform_create stores it AFTER ``.delay``) so nothing is
        revoked — harmless now, because the task cannot claim the row when it
        finally runs (see L2).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.backtests.models import Backtest
from apps.backtests.tasks import ORPHAN_THRESHOLD_MIN, sweep_orphan_backtests
from apps.backtests.walkforward import run_walkforward
from apps.data.models import DailyBar

User = get_user_model()
pytestmark = pytest.mark.django_db

START = dt.date(2023, 1, 2)


def _user():
    return User.objects.create_user(email="review-life@x.test", password="pw-fake-123456789")


def _seed(ticker: str, n: int) -> None:
    price = 100.0
    for i in range(n):
        d = START + dt.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        price *= 1.0 + (0.001 * (1 + i % 3))
        DailyBar.objects.create(
            ticker=ticker, date=d, source="fmp", open=Decimal(f"{price:.4f}"),
            high=Decimal(f"{price:.4f}"), low=Decimal(f"{price:.4f}"),
            close=Decimal(f"{price:.4f}"), adjusted_close=Decimal(f"{price:.4f}"),
            volume=1,
        )


def _det_backtest(user, **kw) -> Backtest:
    fields = dict(
        user=user, name="t", universe=["AAA"], start_date=START,
        end_date=START + dt.timedelta(days=131), is_window_days=126,
        oos_window_days=5, step_days=5, n_candidates=1,
        engine_mode=Backtest.RISK_PARITY, rebalance_frequency="monthly",
    )
    fields.update(kw)
    return Backtest.objects.create(**fields)


# ---------------------------------------------------------------------------
# L1 — a reserved-but-not-yet-active task is NOT swept.
# ---------------------------------------------------------------------------
@patch("hedgefund.celery.app")
def test_L1_sweeper_spares_queued_prefetched_backtest(mock_celery):
    tid = "11111111-2222-3333-4444-555555555555"
    # Worker has the task RESERVED (prefetched) but not ACTIVE: the queue is busy.
    inspect = mock_celery.control.inspect.return_value
    inspect.active.return_value = {"w1": []}
    inspect.reserved.return_value = {"w1": [{"id": tid}]}
    inspect.scheduled.return_value = {"w1": []}
    u = _user()
    bt = _det_backtest(u, status=Backtest.QUEUED, celery_task_id=tid)
    Backtest.objects.filter(pk=bt.pk).update(
        created_at=timezone.now() - dt.timedelta(minutes=ORPHAN_THRESHOLD_MIN + 5)
    )
    out = sweep_orphan_backtests()
    assert out["swept"] == 0
    bt.refresh_from_db()
    assert bt.status == Backtest.QUEUED
    assert bt.error_message == ""
    # The sweeper now asks the worker for reserved (and scheduled) tasks too.
    assert inspect.reserved.called
    assert inspect.scheduled.called


@patch("hedgefund.celery.app")
def test_L1_sweeper_spares_running_backtest_with_fresh_heartbeat(mock_celery):
    """A long fold that writes progress is alive even if the broker reports
    nothing (inspect can come back empty under load)."""
    inspect = mock_celery.control.inspect.return_value
    inspect.active.return_value = {}
    inspect.reserved.return_value = {}
    inspect.scheduled.return_value = {}
    u = _user()
    bt = _det_backtest(u, status=Backtest.RUNNING, celery_task_id="dead-id")
    Backtest.objects.filter(pk=bt.pk).update(
        created_at=timezone.now() - dt.timedelta(minutes=ORPHAN_THRESHOLD_MIN + 5),
        heartbeat_at=timezone.now() - dt.timedelta(minutes=1),
    )
    assert sweep_orphan_backtests()["swept"] == 0
    bt.refresh_from_db()
    assert bt.status == Backtest.RUNNING


@patch("hedgefund.celery.app")
def test_L1_sweeper_still_fails_a_genuinely_orphaned_backtest(mock_celery):
    """Control: no live task id anywhere and a stale heartbeat => swept."""
    inspect = mock_celery.control.inspect.return_value
    inspect.active.return_value = {"w1": []}
    inspect.reserved.return_value = {"w1": []}
    inspect.scheduled.return_value = {"w1": []}
    u = _user()
    bt = _det_backtest(u, status=Backtest.RUNNING, celery_task_id="gone")
    Backtest.objects.filter(pk=bt.pk).update(
        created_at=timezone.now() - dt.timedelta(minutes=ORPHAN_THRESHOLD_MIN + 5),
        heartbeat_at=timezone.now() - dt.timedelta(minutes=ORPHAN_THRESHOLD_MIN + 5),
    )
    assert sweep_orphan_backtests()["swept"] == 1
    bt.refresh_from_db()
    assert bt.status == Backtest.FAILED
    assert "Orphaned" in bt.error_message


# ---------------------------------------------------------------------------
# L2 — run_walkforward refuses to resurrect a terminal backtest.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("terminal", [Backtest.CANCELLED, Backtest.FAILED])
def test_L2_run_walkforward_refuses_to_claim_terminal_status(terminal):
    from apps.backtests.walkforward import BacktestNotClaimable

    _seed("AAA", 140)
    u = _user()
    bt = _det_backtest(
        u, status=terminal, error_message="Cancelled by user.",
        finished_at=timezone.now(),
    )
    with pytest.raises(BacktestNotClaimable):
        run_walkforward(bt)  # what tasks.run_backtest calls
    bt.refresh_from_db()
    assert bt.status == terminal                     # terminal state preserved
    assert bt.error_message == "Cancelled by user."
    assert getattr(bt, "metrics", None) is None      # never becomes gate evidence


def test_L2_run_backtest_task_swallows_the_unclaimable_row():
    """tasks.run_backtest must not rewrite the terminal status either — a
    cancelled run that later gets a worker slot stays cancelled."""
    from apps.backtests.tasks import run_backtest

    _seed("AAA", 140)
    u = _user()
    bt = _det_backtest(u, status=Backtest.CANCELLED, error_message="Cancelled by user.")
    run_backtest(bt.id)
    bt.refresh_from_db()
    assert bt.status == Backtest.CANCELLED
    assert bt.error_message == "Cancelled by user."


def test_L2_claim_is_conditional_so_only_one_worker_runs_a_backtest():
    _seed("AAA", 140)
    u = _user()
    bt = _det_backtest(u, status=Backtest.QUEUED)
    run_walkforward(bt)                       # first claim wins
    bt.refresh_from_db()
    assert bt.status == Backtest.DONE
    assert bt.heartbeat_at is not None        # progress writes a heartbeat

    from apps.backtests.walkforward import BacktestNotClaimable

    dup = Backtest.objects.get(pk=bt.pk)
    with pytest.raises(BacktestNotClaimable):  # a duplicate delivery is refused
        run_walkforward(dup)
    bt.refresh_from_db()
    assert bt.status == Backtest.DONE


def test_L2_run_backtest_declares_celery_time_limits():
    """A wedged provider connection must not pin a prefork slot forever."""
    from apps.backtests import tasks as t

    assert t.run_backtest.soft_time_limit == t.RUN_BACKTEST_SOFT_TIME_LIMIT
    assert t.run_backtest.time_limit == t.RUN_BACKTEST_TIME_LIMIT
    assert t.RUN_BACKTEST_SOFT_TIME_LIMIT == 6 * 60 * 60
    assert t.RUN_BACKTEST_TIME_LIMIT > t.RUN_BACKTEST_SOFT_TIME_LIMIT


# ---------------------------------------------------------------------------
# L3 — cancel before celery_task_id is persisted revokes nothing.
# ---------------------------------------------------------------------------
def test_L3_cancel_with_empty_task_id_revokes_nothing():
    from rest_framework.test import APIClient

    u = _user()
    bt = _det_backtest(u, status=Backtest.QUEUED, celery_task_id="")
    c = APIClient()
    c.force_authenticate(u)
    with patch("apps.backtests.views.celery_app") as app:
        r = c.post(f"/api/backtests/{bt.id}/cancel/")
    assert r.status_code == 200
    assert not app.control.revoke.called
    bt.refresh_from_db()
    assert bt.status == Backtest.CANCELLED
    # ...and when the queued task eventually runs it can no longer claim the row
    # (L2), so the cancel sticks even though nothing was revoked.
    from apps.backtests.tasks import run_backtest

    run_backtest(bt.id)
    bt.refresh_from_db()
    assert bt.status == Backtest.CANCELLED


def test_L3_cancel_uses_a_conditional_update_so_it_cannot_clobber_a_finish():
    """A cancel that races the worker's own terminal write must lose, not
    overwrite DONE with CANCELLED."""
    from rest_framework.test import APIClient

    u = _user()
    bt = _det_backtest(u, status=Backtest.RUNNING, celery_task_id="tid-1")
    c = APIClient()
    c.force_authenticate(u)

    # revoke() is the last thing before the write, so flipping the row there
    # simulates the worker finishing between the view's fetch and its UPDATE.
    def _finish_mid_flight(*_a, **_kw):
        Backtest.objects.filter(pk=bt.pk).update(status=Backtest.DONE)

    with patch("apps.backtests.views.celery_app") as app:
        app.control.revoke.side_effect = _finish_mid_flight
        r = c.post(f"/api/backtests/{bt.id}/cancel/")
    assert app.control.revoke.called
    assert app.control.revoke.call_args.kwargs["terminate"] is True
    assert r.status_code == 409
    bt.refresh_from_db()
    assert bt.status == Backtest.DONE          # the finish is not clobbered


def test_L3_cancel_of_a_running_backtest_revokes_with_terminate():
    from rest_framework.test import APIClient

    u = _user()
    bt = _det_backtest(u, status=Backtest.RUNNING, celery_task_id="tid-2")
    c = APIClient()
    c.force_authenticate(u)
    with patch("apps.backtests.views.celery_app") as app:
        r = c.post(f"/api/backtests/{bt.id}/cancel/")
    assert r.status_code == 200
    assert app.control.revoke.call_args.args[0] == "tid-2"
    assert app.control.revoke.call_args.kwargs["terminate"] is True
    bt.refresh_from_db()
    assert bt.status == Backtest.CANCELLED
