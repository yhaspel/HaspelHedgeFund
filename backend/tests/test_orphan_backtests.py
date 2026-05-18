"""sweep_orphan_backtests marks abandoned backtests as failed.

Mirrors tests/test_runs_api.py orphan-run coverage. Uses the same
freezegun-free trick: write created_at directly past the threshold.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.backtests.models import Backtest
from apps.backtests.tasks import ORPHAN_THRESHOLD_MIN, sweep_orphan_backtests

pytestmark = pytest.mark.django_db


def _user() -> User:
    return User.objects.create_user(email="bt@x.com", password="x")


def _bt(user, status, created_offset_min=20, celery_task_id="") -> Backtest:
    bt = Backtest.objects.create(
        user=user, name="t", universe=["AAA"],
        start_date=dt.date(2024, 1, 1), end_date=dt.date(2024, 12, 31),
        starting_cash=Decimal("100000"),
        is_window_days=126, oos_window_days=42, step_days=42,
        n_candidates=5, status=status, celery_task_id=celery_task_id,
    )
    # Force created_at backwards past the orphan threshold.
    past = timezone.now() - dt.timedelta(minutes=created_offset_min)
    Backtest.objects.filter(pk=bt.pk).update(created_at=past)
    bt.refresh_from_db()
    return bt


@patch("hedgefund.celery.app")
def test_sweeps_old_running_with_no_active_task(mock_celery):
    mock_celery.control.inspect.return_value.active.return_value = {"w": []}
    u = _user()
    bt = _bt(u, status="running", created_offset_min=ORPHAN_THRESHOLD_MIN + 5)
    out = sweep_orphan_backtests()
    assert out["swept"] == 1
    bt.refresh_from_db()
    assert bt.status == "failed"
    assert "Orphaned" in bt.error_message
    assert bt.finished_at is not None


@patch("hedgefund.celery.app")
def test_skips_recent_backtest(mock_celery):
    mock_celery.control.inspect.return_value.active.return_value = {"w": []}
    u = _user()
    bt = _bt(u, status="running", created_offset_min=2)  # below threshold
    out = sweep_orphan_backtests()
    assert out["swept"] == 0
    bt.refresh_from_db()
    assert bt.status == "running"


@patch("hedgefund.celery.app")
def test_skips_backtest_with_active_celery_task(mock_celery):
    mock_celery.control.inspect.return_value.active.return_value = {
        "w": [{"id": "abc123"}]
    }
    u = _user()
    bt = _bt(
        u, status="running",
        created_offset_min=ORPHAN_THRESHOLD_MIN + 5,
        celery_task_id="abc123",
    )
    out = sweep_orphan_backtests()
    assert out["swept"] == 0
    bt.refresh_from_db()
    assert bt.status == "running"


@patch("hedgefund.celery.app")
def test_broker_unreachable_aborts_safely(mock_celery):
    mock_celery.control.inspect.side_effect = RuntimeError("broker down")
    u = _user()
    bt = _bt(u, status="running", created_offset_min=ORPHAN_THRESHOLD_MIN + 5)
    out = sweep_orphan_backtests()
    assert out.get("error") == "inspect_failed"
    bt.refresh_from_db()
    assert bt.status == "running"  # unchanged — don't clobber on broker failure
