import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hedgefund.settings.dev")

app = Celery("hedgefund")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Default periodic schedule. The DatabaseScheduler picks these up at startup
# and any operator-edited PeriodicTask rows override them.
app.conf.beat_schedule = {
    "sweep-orphan-runs": {
        "task": "apps.runs.tasks.sweep_orphan_runs",
        "schedule": 300.0,  # every 5 min
    },
    "prewarm-macro-snapshot": {
        "task": "apps.data.tasks.prewarm_macro_snapshot",
        "schedule": crontab(minute=15, hour=6),  # 06:15 UTC daily
    },
}
