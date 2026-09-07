"""Delete the stored ``ingest-13f-datasets`` beat entry (WAVE-3 P2 item 2).

Beat runs on ``django_celery_beat``'s ``DatabaseScheduler``: entries from
``app.conf.beat_schedule`` are persisted as ``PeriodicTask`` rows, and removing
one from the config does **not** delete its row. Without this, a deployed
instance keeps firing the quarterly SEC bulk 13F ingest that WAVE-3 P2 removed.

Idempotent, and a no-op when ``django_celery_beat`` is not installed (tests).
"""

from django.db import migrations

BEAT_ENTRY_NAME = "ingest-13f-datasets"
DEAD_TASK_PATH = "apps.data.tasks.ingest_13f_current_quarter"


def drop_beat_entry(apps, schema_editor):
    try:
        PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    except LookupError:  # pragma: no cover - app not installed
        return
    PeriodicTask.objects.filter(name=BEAT_ENTRY_NAME).delete()
    PeriodicTask.objects.filter(task=DEAD_TASK_PATH).delete()


def noop_reverse(apps, schema_editor):
    """Deliberately not restored: the task it pointed at is a tombstone."""


class Migration(migrations.Migration):
    dependencies = [
        ("data", "0011_wave3_drop_dead_13f_models"),
        ("django_celery_beat", "0001_initial"),
    ]

    operations = [migrations.RunPython(drop_beat_entry, noop_reverse)]
