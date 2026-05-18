"""Log-record filter that scrubs secret-bearing query params from URLs.

Attached to the root handler in settings.LOGGING. Applies to every log
record before it hits any handler, so any logger that accidentally
echoes an authenticated URL (httpx INFO request lines, custom debug
logs, exception messages) is automatically sanitized.

Add new param names to `SECRET_QUERY_KEYS` if a future provider uses a
different query-string credential.
"""
from __future__ import annotations

import logging
import re

SECRET_QUERY_KEYS = ("token", "apikey", "api_key", "access_token", "auth_token")

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
