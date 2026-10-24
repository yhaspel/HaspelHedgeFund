"""sweep_orphan_runs fails runs that have no Celery task id on age alone.

The synchronous schedule fan-out (and the pairs-cycle audit rows) execute a run
in-process, so its celery_task_id is empty and inspect.active() can say nothing
about it. A run left RUNNING by a process that was replaced mid-execution used
to stay RUNNING forever; past TASKLESS_ORPHAN_THRESHOLD_HOURS it is now failed
the same way as a dispatched orphan. Same freezegun-free trick as
tests/test_orphan_backtests.py: write created_at directly.
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.runs.models import Run
from apps.runs.tasks import TASKLESS_ORPHAN_THRESHOLD_HOURS, sweep_orphan_runs

pytestmark = pytest.mark.django_db


class _Inspect:
    def __init__(self, active):
        self._active = active

    def active(self):
        return self._active


@pytest.fixture
def user():
    return User.objects.create_user(email="orphan-runs@x.test", password="x")


def _run(user, *, status, age, celery_task_id=""):
    run = Run.objects.create(
        user=user, tickers=["AAPL"], as_of_date=dt.date(2026, 6, 1),
        status=status, celery_task_id=celery_task_id,
    )
    Run.objects.filter(pk=run.pk).update(created_at=timezone.now() - age)
    return run


def _sweep(replies):
    with patch("hedgefund.celery.app.control.inspect", return_value=_Inspect(replies)), \
         patch("apps.notifications.operator.notify_orphan_sweep") as alert:
        out = sweep_orphan_runs()
    return out, alert


def test_taskless_run_left_running_by_a_replaced_process_is_swept(user):
    stuck = _run(user, status=Run.RUNNING, age=dt.timedelta(days=45))
    out, alert = _sweep({"worker1": []})
    assert out == {"swept": 1}
    stuck.refresh_from_db()
    assert stuck.status == Run.FAILED
    assert stuck.error_message.startswith("Orphaned: no Celery task id")
    assert stuck.finished_at is not None
    alert.assert_called_once_with(1, run_ids=[stuck.id])


def test_taskless_queued_run_past_the_threshold_is_swept(user):
    lost = _run(
        user, status=Run.QUEUED,
        age=dt.timedelta(hours=TASKLESS_ORPHAN_THRESHOLD_HOURS, minutes=5),
    )
    out, _alert = _sweep({"worker1": []})
    assert out == {"swept": 1}
    lost.refresh_from_db()
    assert lost.status == Run.FAILED
    assert "still queued" in lost.error_message


def test_recent_taskless_runs_are_untouched(user):
    # Far past the window a DISPATCHED run gets, but inside the task-less
    # threshold: a child of a long scheduled batch is still legitimately live.
    running = _run(user, status=Run.RUNNING, age=dt.timedelta(hours=2))
    queued = _run(
        user, status=Run.QUEUED,
        age=dt.timedelta(hours=TASKLESS_ORPHAN_THRESHOLD_HOURS - 1),
    )
    out, alert = _sweep({"worker1": []})
    assert out == {"swept": 0}
    running.refresh_from_db()
    queued.refresh_from_db()
    assert running.status == Run.RUNNING
    assert queued.status == Run.QUEUED
    assert not alert.called


def test_taskless_sweep_does_not_wait_for_a_worker_reply(user):
    """No worker reply aborts the dispatched pass (it cannot vouch for those
    runs) but says nothing about task-less ones, so they are still swept."""
    stuck = _run(user, status=Run.RUNNING, age=dt.timedelta(days=2))
    dispatched = _run(
        user, status=Run.RUNNING, celery_task_id="task-abc", age=dt.timedelta(hours=1),
    )
    out, alert = _sweep(None)
    assert out == {"swept": 1, "error": "no_worker_reply"}
    stuck.refresh_from_db()
    dispatched.refresh_from_db()
    assert stuck.status == Run.FAILED
    assert dispatched.status == Run.RUNNING
    alert.assert_called_once_with(1, run_ids=[stuck.id])


def test_runs_with_a_task_id_behave_as_before(user):
    """The age-only rule never applies to a run that has a task id: a live one
    stays, a dead RUNNING one is swept by the inspect path, and a QUEUED one is
    left alone however old. One alert covers both kinds of sweep."""
    alive = _run(
        user, status=Run.RUNNING, celery_task_id="task-live", age=dt.timedelta(hours=1),
    )
    dead = _run(
        user, status=Run.RUNNING, celery_task_id="task-dead", age=dt.timedelta(hours=1),
    )
    waiting = _run(
        user, status=Run.QUEUED, celery_task_id="task-queued", age=dt.timedelta(days=2),
    )
    stuck = _run(user, status=Run.RUNNING, age=dt.timedelta(days=2))
    out, alert = _sweep({"worker1": [{"id": "task-live"}]})
    assert out == {"swept": 2}
    for r in (alive, dead, waiting, stuck):
        r.refresh_from_db()
    assert alive.status == Run.RUNNING
    assert waiting.status == Run.QUEUED
    assert dead.status == Run.FAILED
    assert dead.error_message.startswith("Orphaned: no active Celery task")
    assert stuck.status == Run.FAILED
    alert.assert_called_once()
    assert alert.call_args.args == (2,)
    assert sorted(alert.call_args.kwargs["run_ids"]) == sorted([dead.id, stuck.id])
