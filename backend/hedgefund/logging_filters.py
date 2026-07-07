"""Log-record filter that scrubs secret-bearing query params from URLs.

Attached to the root handler in settings.LOGGING. Applies to every log
record before it hits any handler, so any logger that accidentally
echoes an authenticated URL (httpx INFO request lines, custom debug
logs, exception messages) is automatically sanitized.

Add new param names to `SECRET_QUERY_KEYS` if a future provider uses a
different query-string credential.
"""
from __future__ import annotations

import contextvars
import logging
import re

SECRET_QUERY_KEYS = ("token", "apikey", "api_key", "access_token", "auth_token")

# P5-SH WS2.1 — greppable request/run correlation. Bound by RequestIdMiddleware
# (HTTP) and the Celery task signals / execute_run (worker), read by ContextFilter
# so every JSON log line carries whichever ids are in scope. Contextvars are
# task/thread-safe: a value set on one request/task never leaks into another.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
run_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default="")


class ContextFilter(logging.Filter):
    """Inject the current request_id / run_id (empty string when unset) onto
    every record so the JSON formatter can emit them as first-class fields."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get("")
        record.run_id = run_id_var.get("")
        return True

# Match `key=value` where value is anything up to the next & or whitespace
# or quote. Case-insensitive on the key.
_PATTERN = re.compile(
    r"(?i)([?&](?:" + "|".join(SECRET_QUERY_KEYS) + r")=)([^&\s\"']+)"
)
_REDACTED = r"\1REDACTED"


def _scrub(value: object) -> object:
    if not isinstance(value, str):
        return value
    return _PATTERN.sub(_REDACTED, value)


class RedactSecretsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _scrub(record.msg)  # type: ignore[assignment]
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(_scrub(a) for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: _scrub(v) for k, v in record.args.items()}
        return True
