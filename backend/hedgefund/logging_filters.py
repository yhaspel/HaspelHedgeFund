"""Log-record filter that scrubs secret-bearing query params from URLs.

Attached to the root handler and the httpx logger in settings.LOGGING, and to
the Celery worker/beat handlers (hedgefund/celery.py), so any logger that
accidentally echoes an authenticated URL (httpx INFO request lines, custom
debug logs, exception messages) is automatically sanitized.

Add new param names to `SECRET_QUERY_KEYS` if a future provider uses a
different query-string credential.
"""
from __future__ import annotations

import contextvars
import logging
import re
import traceback

SECRET_QUERY_KEYS = ("token", "apikey", "api_key", "access_token", "auth_token", "key")

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
# or quote. Case-insensitive on the key. The key is matched on a word boundary
# (not just after `?`/`&`) so a bare `apikey=...` inside a prose log line or an
# exception message is scrubbed too — provider errors quote the whole URL.
# Longest names first so `access_token=` is not partially matched as `token=`.
_KEYS_ALT = "|".join(sorted(SECRET_QUERY_KEYS, key=len, reverse=True))
_PATTERN = re.compile(r"(?i)\b(" + _KEYS_ALT + r")=([^&\s\"'\\]+)")
_REDACTED = r"\1=REDACTED"

# Telegram bot tokens live in the URL *path* (`/bot<digits>:<secret>/sendMessage`),
# so the query-param rule above cannot see them. httpx logs every request line at
# INFO; this makes such a line safe even if the httpx logger is re-enabled.
_BOT_TOKEN_PATTERN = re.compile(r"(?i)\bbot\d{4,}:[A-Za-z0-9_\-]+")
_BOT_REDACTED = "botREDACTED"

# Credentials that travel in headers rather than URLs, should one ever be echoed
# (a logged header dict, a provider error quoting the request): bearer tokens,
# and OpenRouter / Anthropic keys anywhere in the text.
_BEARER_PATTERN = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9\-._~+/]{8,}=*")
_PROVIDER_KEY_PATTERN = re.compile(r"\b(sk-(?:or-v1|ant)-)[A-Za-z0-9_\-]+")


def _scrub_text(value: str) -> str:
    value = _BOT_TOKEN_PATTERN.sub(_BOT_REDACTED, _PATTERN.sub(_REDACTED, value))
    value = _BEARER_PATTERN.sub(r"\1REDACTED", value)
    return _PROVIDER_KEY_PATTERN.sub(r"\1REDACTED", value)


def scrub_secrets(value: str) -> str:
    """Public form of the redaction, for strings that are STORED rather than
    logged (e.g. a delivery error persisted on NotificationEvent and returned
    by the API) — the log filter never sees those."""
    return _scrub_text(value) if isinstance(value, str) else value


class RedactSecretsFilter(logging.Filter):
    """Scrub credentials out of the formatted message AND the traceback.

    The message is formatted here (``getMessage()``) and scrubbed whole, so an
    argument of any type is covered — an ``httpx.HTTPStatusError`` or
    ``httpx.URL`` whose ``str()`` carries ``?apikey=...`` included. ``msg``
    becomes that text and ``args`` becomes ``()``: exactly what a formatter
    would have rendered, ``%d``/``%s`` and all.

    ``log.exception(...)`` attaches the live exception via ``exc_info``; the
    formatter renders it later, *after* every filter has run, so scrubbing the
    message alone leaves the provider URL (``?apikey=...``) in the traceback.
    We therefore pre-render the traceback here, scrub it, park it on
    ``record.exc_text`` and clear ``exc_info`` — the stdlib formatter uses a
    pre-rendered ``exc_text``, and ``pythonjsonlogger`` would re-render a live
    ``exc_info``. Idempotent: a second handler sees a pre-formatted ``msg``
    with no ``args``, ``exc_info=None`` and an already-clean ``exc_text``.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a bad format/args pair must not raise here
            message = f"{record.msg} {record.args!r}"
        record.msg = _scrub_text(message)
        record.args = ()
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = "".join(
                    traceback.format_exception(*record.exc_info)
                ).rstrip("\n")
            record.exc_info = None
        if record.exc_text:
            record.exc_text = _scrub_text(record.exc_text)
        if record.stack_info:
            record.stack_info = _scrub_text(record.stack_info)
        return True
