"""P5-SH WS2.1 — request-id log correlation middleware.

Binds a ``request_id`` contextvar for the lifetime of each HTTP request so every
JSON log line emitted while handling it is greppable per request. Honors an
inbound ``X-Request-ID`` (so a reverse proxy / caller can thread its own id) and
otherwise mints a UUID4; the value is echoed back on the response header.
"""
from __future__ import annotations

import re
import uuid

from .logging_filters import request_id_var

# A caller-supplied id must be short and boring — no CRLF (header-injection /
# BadHeaderError when we echo it back) and no exotic characters. Anything that
# doesn't match is discarded in favour of a fresh UUID.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        supplied = request.headers.get("X-Request-ID", "")
        rid = supplied if _SAFE_REQUEST_ID.match(supplied) else uuid.uuid4().hex
        token = request_id_var.set(rid)
        try:
            response = self.get_response(request)
        finally:
            request_id_var.reset(token)
        response["X-Request-ID"] = rid
        return response
