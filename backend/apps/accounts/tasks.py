from datetime import UTC, datetime

from celery import shared_task


@shared_task
def ping() -> str:
    return datetime.now(UTC).isoformat()
