"""P5-SH WS2.3 — optional Sentry error reporting (default OFF).

Self-hosting requires zero third-party observability accounts, so Sentry is an
*opt-in* extra: nothing here is imported or initialized unless ``SENTRY_DSN`` is
set (``init_sentry`` is only called from settings when the DSN is non-empty, and
``sentry_sdk`` itself is imported lazily inside this function). If the DSN is set
but the ``sentry`` extra isn't installed, we warn and continue rather than block
startup.
"""
from __future__ import annotations

import os
import sys


def init_sentry(dsn: str, *, environment: str = "") -> bool:
    """Initialize Sentry if `dsn` is non-empty and sentry-sdk is installed.
    Returns True when Sentry was initialized, False otherwise. Never raises."""
    if not dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.django import DjangoIntegration
    except ImportError:
        print(
            "SENTRY_DSN is set but sentry-sdk is not installed; skipping Sentry. "
            "Install the optional extra: uv sync --extra sentry (or pip install "
            "'sentry-sdk').",
            file=sys.stderr,
        )
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=environment or "unknown",
        integrations=[DjangoIntegration(), CeleryIntegration()],
        # Off by default — errors only, no perf tracing — unless the operator opts in.
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0") or "0"),
        send_default_pii=False,
    )
    return True
