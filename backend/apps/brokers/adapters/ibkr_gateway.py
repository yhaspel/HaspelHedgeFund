"""Thin HTTP client for IBKR's Client Portal Gateway (P3a-2).

This module is pure transport. It owns the `httpx.Client`, knows the
`/v1/api` URL prefix, knows the cert-verification rule (skip only for
`settings.IBKR_GATEWAY_BASE_URL`), and exposes a small set of session-
lifecycle helpers used directly by `IBKRBroker`:

- `tickle()` — POST /v1/api/tickle, keep-alive + pre-login probe.
- `auth_status()` — POST /v1/api/iserver/auth/status.
- `reply(reply_id, confirmed=…)` — POST /v1/api/iserver/reply/{replyId},
  the transport for the order-confirmation reply loop. The reply-loop
  *policy* (allowlist, iteration cap) lives in `ibkr.py`.

The generic `request()` / `get()` / `post()` / `delete()` are used by
`IBKRBroker` for the portfolio / iserver endpoints.

Exception contract — consistent with `apps/brokers/interfaces.py`:

- `BrokerTransientError` on connection error, timeout, 5xx, or invalid
  JSON. The outcome is genuinely unknown — `submit_idempotent` parks
  the order in `idempotency_state="unknown"` and the next reconcile
  pass adopts via `find_order_by_client_id` (ADR 0008 / 0011).
- `BrokerError` on any 4xx. Caller decides whether to surface it (e.g.
  a 400 from a malformed order) or recover (e.g. a duplicate-`cOID`
  rejection from `submit_order` — see Risks #7c).

Cert verification is disabled only when `base_url ==
settings.IBKR_GATEWAY_BASE_URL`. Any other URL keeps verification on so
a misconfigured / hostile host never inherits the self-signed-cert
trust. See ADR 0011 §1 and Risks #3.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from django.conf import settings

from ..interfaces import BrokerError, BrokerTransientError

log = logging.getLogger(__name__)

API_PREFIX = "/v1/api"
DEFAULT_TIMEOUT = 30.0


def _verify_for(base_url: str) -> bool:
    """Cert verification rule. Off only for the configured gateway URL."""
    return base_url.rstrip("/") != str(settings.IBKR_GATEWAY_BASE_URL).rstrip("/")


class IBKRGatewaySession:
    """Per-call gateway client. Stateless beyond its `httpx.Client`."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = (
            base_url or str(settings.IBKR_GATEWAY_BASE_URL)
        ).rstrip("/")
        self._client = httpx.Client(
            verify=_verify_for(self.base_url),
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> IBKRGatewaySession:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- Generic HTTP -----------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict | None = None,
    ) -> Any:
        """Issue one request. `path` is relative to the /v1/api base.

        Returns the decoded JSON body on 2xx. Raises BrokerTransientError
        for unknown-outcome errors (network, timeout, 5xx, bad JSON) and
        BrokerError for any 4xx (caller decides how to interpret).
        """
        url = f"{self.base_url}{API_PREFIX}{path}"
        try:
            resp = self._client.request(method, url, json=json, params=params)
        except httpx.RequestError as exc:
            raise BrokerTransientError(
                f"gateway {method} {path} failed: {type(exc).__name__}: {exc}"
            ) from exc

        if 500 <= resp.status_code < 600:
            raise BrokerTransientError(
                f"gateway {method} {path}: HTTP {resp.status_code}: "
                f"{resp.text[:200]}"
            )
        if 400 <= resp.status_code < 500:
            raise BrokerError(
                f"gateway {method} {path}: HTTP {resp.status_code}: "
                f"{resp.text[:500]}"
            )

        # 2xx — parse body. Some endpoints (DELETE, /tickle pre-login) can
        # return an empty body; treat that as `None`.
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError as exc:
            raise BrokerTransientError(
                f"gateway {method} {path}: invalid JSON: {exc}"
            ) from exc

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.request("POST", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.request("DELETE", path, **kwargs)

    # -- Session lifecycle helpers ---------------------------------------

    def tickle(self) -> Any:
        """POST /tickle. IBKR's documented keep-alive — also the canonical
        pre-login reachability probe, since it returns session info either
        way."""
        return self.post("/tickle")

    def auth_status(self) -> dict:
        """POST /iserver/auth/status. Returns at least
        `{authenticated: bool, connected: bool, competing: bool}`. POST
        per IBKR's spec — some clients use GET and the gateway is lenient,
        but the spec is POST."""
        result = self.post("/iserver/auth/status", json={})
        return result if isinstance(result, dict) else {}

    def reply(self, reply_id: str, *, confirmed: bool) -> Any:
        """POST /iserver/reply/{replyId} body `{confirmed: bool}`. Transport
        only — the allowlist / iteration cap policy is in `ibkr.py` (ADR
        0011 §3)."""
        return self.post(f"/iserver/reply/{reply_id}", json={"confirmed": confirmed})

    def is_authenticated(self) -> bool:
        """Convenience: True iff /iserver/auth/status reports the session
        is fully authenticated AND connected to the broker. Used by the
        connect-flow auth-status poll and by `keep_gateway_warm`."""
        status = self.auth_status()
        return bool(status.get("authenticated")) and bool(status.get("connected"))
