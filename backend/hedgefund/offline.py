"""Offline-mode helpers (P4-OFF).

``OFFLINE_MODE`` (env-gated, ``settings.OFFLINE_MODE``) puts the backend in
"internet down, local stack up" (L1) mode: external data providers are fenced
at their HTTP seam, LLM calls are forced onto the local Ollama model, and the
outward-facing periodic tasks (provider refresh, notifications, broker polling)
no-op fast instead of spamming connection errors every beat.

This module holds only the tiny, dependency-free predicates shared across apps.
The LLM/preset side lives in ``apps.models_catalog.offline`` and the provider
HTTP fence in ``apps.data.providers._http``.
"""
from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

log = logging.getLogger(__name__)


def is_offline() -> bool:
    """True when the backend is running in offline (L1) mode."""
    return bool(getattr(settings, "OFFLINE_MODE", False))


def skip_when_offline(task: str, result: Any = None) -> bool:
    """Early-return guard for outward-facing Celery tasks.

    Usage at the top of a task that talks to the outside world::

        if skip_when_offline("prewarm_macro_snapshot"):
            return {"status": "skipped_offline"}

    Logs one structured line so an operator can see the task was intentionally
    skipped (not silently dead).
    """
    if is_offline():
        log.info("offline_skip task=%s reason=OFFLINE_MODE", task)
        return True
    return False
