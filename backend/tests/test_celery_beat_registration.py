"""CI guard: every task named in the static Celery beat schedule must
actually be registered with the app via autodiscover.

This catches the wiring bug where a task module is never imported by
`autodiscover_tasks()` (which only loads each app's `tasks` module), so a
sibling module like `apps.portfolios.tasks_autopilot` defines `@shared_task`s
that beat then dispatches into the void as "Received unregistered task". Unit
tests that call such tasks as plain functions pass while the scheduled path is
silently broken — exactly how this slipped through for the P7 autopilot.

The check runs in a FRESH interpreter on purpose: registration is a global
side effect, so other test files that `import tasks_autopilot` would otherwise
populate the registry and mask a broken beat wiring. A subprocess sees only
what a real worker's autodiscover imports.
"""
from __future__ import annotations

import os
import subprocess
import sys

_PROBE = """
import django
django.setup()
from hedgefund.celery import app
app.loader.import_default_modules()  # what a worker does at startup
scheduled = {e["task"] for e in app.conf.beat_schedule.values()}
missing = sorted(scheduled - set(app.tasks))
print("MISSING=" + ",".join(missing))
"""


def test_all_beat_scheduled_tasks_are_registered():
    from django.conf import settings

    # Run the probe with the SAME settings module the test process resolved,
    # so it sees the same INSTALLED_APPS / beat_schedule a real run does —
    # pytest may select test settings via pyproject while the container env
    # points DJANGO_SETTINGS_MODULE at dev.
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": settings.SETTINGS_MODULE}
    result = subprocess.run(
        [sys.executable, "-c", _PROBE], capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, (
        f"probe subprocess failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    line = next(
        (ln for ln in result.stdout.splitlines() if ln.startswith("MISSING=")), None,
    )
    assert line is not None, (
        f"probe produced no MISSING line:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    missing = line.split("=", 1)[1]
    assert missing == "", (
        "beat schedule references tasks autodiscover never registers "
        f"(their module is not imported by any app's tasks.py): {missing}"
    )
