import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hedgefund.settings.dev")

app = Celery("hedgefund")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
