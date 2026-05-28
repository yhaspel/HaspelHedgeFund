"""TradeStation OAuth 2.0 authorization-code helper (P3a-3).

Stateless module — every function takes inputs and returns values; no
module-level state. State persistence (the per-account draft, the
encrypted tokens) lives in `models.BrokerAccount` / `BrokerCredential`.

Three responsibilities:

1.  `build_authorize_url` — assemble the TradeStation `/authorize` URL with
    `client_id`, `redirect_uri`, requested scopes, a signed `state` (CSRF +
    BrokerAccount binding), and a PKCE `code_challenge`.

2.  `exchange_code_for_tokens` — POST `/oauth/token` with the auth code +
    PKCE verifier and the developer-app `client_secret`. Returns access
    token, refresh token, expiry, and granted scopes.

3.  `refresh_if_needed` — refresh an OAuth credential when its access
    token is within `REFRESH_MARGIN` of expiry. Persists rotated tokens
    on the credential.

ADR 0012 records the design decisions (PKCE, env-bound state, BYO
client credentials, environment routing via base_url not scope).
"""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.core.signing import BadSignature, TimestampSigner
from django.utils import timezone

from apps.models_catalog.crypto import decrypt, encrypt

from ..credentials import set_oauth_tokens
from ..interfaces import BrokerError, BrokerTransientError
from ..models import BrokerAccount, BrokerCredential, UserBrokerOAuthApp

log = logging.getLogger(__name__)

# Refresh when the access token has less than this margin remaining.
REFRESH_MARGIN = timedelta(minutes=2)

# `state` validity window — the user has this long to complete consent.
STATE_MAX_AGE_SECONDS = 600

# Used to namespace the TimestampSigner so a leaked signed value from
# another part of the system can't be replayed against the OAuth callback.
_STATE_SALT = "brokers.tradestation.oauth.state"

# Test seam — tests patch `_now()` / `_post()` without monkeypatching the
# stdlib. Keeping a tiny seam keeps adapter tests deterministic.
_DEFAULT_TIMEOUT = 15


def _now():
    return timezone.now()


# --- Per-user developer-app credentials -------------------------------------


def resolve_app_credentials(user) -> tuple[str, str, str]:
    """Return (client_id, client_secret, source).

    `source` is "user" when a `UserBrokerOAuthApp` row supplied the values,
    "env" when falling back to Django settings, "" when neither is
    configured. BYOK precedence: user row wins so a deployer can pre-seed
    a shared app while still letting any user override with their own.
    """
    row = (
        UserBrokerOAuthApp.objects.filter(user=user, broker="tradestation")
        .first()
        if user and getattr(user, "is_authenticated", False) else None
    )
    if row and row.has_secret():
        try:
            cid = decrypt(row.encrypted_client_id)
            csec = decrypt(row.encrypted_client_secret)
        except Exception:  # noqa: BLE001
            cid, csec = "", ""
        if cid and csec:
            return cid, csec, "user"
    cid_env = settings.TRADESTATION_CLIENT_ID
    csec_env = settings.TRADESTATION_CLIENT_SECRET
    if cid_env and csec_env:
        return cid_env, csec_env, "env"
    return "", "", ""


def set_user_app_credentials(user, *, client_id: str, client_secret: str) -> None:
    """Upsert the user's TradeStation developer-app credentials.
    Empty values clear the row's encrypted fields (so the resolver
    falls back to the env)."""
    row, _ = UserBrokerOAuthApp.objects.get_or_create(
        user=user, broker="tradestation",
    )
    row.encrypted_client_id = encrypt(client_id) if client_id else ""
    row.encrypted_client_secret = encrypt(client_secret) if client_secret else ""
    row.save()


# --- PKCE -------------------------------------------------------------------


def make_pkce_pair() -> tuple[str, str]:
    """Return (verifier, challenge). RFC 7636 §4.1/§4.2 — 43-128 char
    URL-safe verifier; S256 challenge is the URL-safe-base64 SHA-256."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# --- state ------------------------------------------------------------------


def sign_state(account_id: int, user_id: int) -> str:
    """Sign a `state` carrying the BrokerAccount + user binding. The
    callback verifies the signature, max-age, and that the embedded
    user_id matches the authenticated request — or, when the callback
    is hit unauthenticated (the typical browser-redirect path), uses
    the binding to re-authenticate the account context."""
    signer = TimestampSigner(salt=_STATE_SALT)
    return signer.sign(f"{account_id}:{user_id}")


def verify_state(value: str) -> tuple[int, int]:
    """Return (account_id, user_id). Raises BrokerError on tamper/expiry."""
    signer = TimestampSigner(salt=_STATE_SALT)
    try:
        payload = signer.unsign(value, max_age=STATE_MAX_AGE_SECONDS)
    except BadSignature as exc:
        raise BrokerError(f"invalid or expired OAuth state: {exc}") from exc
    try:
        acc_str, user_str = payload.split(":", 1)
        return int(acc_str), int(user_str)
    except (ValueError, AttributeError) as exc:
        raise BrokerError("OAuth state payload is malformed") from exc


# --- authorize URL ----------------------------------------------------------


@dataclass(frozen=True)
class AuthorizeRequest:
    url: str
    state: str
    code_verifier: str


def build_authorize_url(*, account: BrokerAccount) -> AuthorizeRequest:
    client_id, _csec, _source = resolve_app_credentials(account.user)
    if not client_id:
        raise BrokerError(
            "TradeStation developer-app credentials are not configured. "
            "Enter your client_id and client_secret on the connect wizard "
            "(or in Settings → Broker apps), or set TRADESTATION_CLIENT_ID "
            "and TRADESTATION_CLIENT_SECRET in your deployment env. See "
            "the in-app guide.",
        )
    state = sign_state(account.pk, account.user_id)
    verifier, challenge = make_pkce_pair()
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": settings.TRADESTATION_REDIRECT_URI,
        "audience": "https://api.tradestation.com",
        "scope": settings.TRADESTATION_SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return AuthorizeRequest(
        url=f"{settings.TRADESTATION_AUTHORIZE_URL}?{urlencode(params)}",
        state=state,
        code_verifier=verifier,
    )


# --- token exchange ---------------------------------------------------------


@dataclass(frozen=True)
class TokenBundle:
    access_token: str
    refresh_token: str
    expires_in: int        # seconds
    scopes: str
    raw: dict


def _post(url: str, data: dict) -> dict:
    """Test seam. Wraps requests.post and normalises errors."""
    try:
        r = requests.post(url, data=data, timeout=_DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        raise BrokerTransientError(f"TradeStation token endpoint: {exc}") from exc
    if r.status_code >= 500:
        raise BrokerTransientError(
            f"TradeStation token endpoint {r.status_code}: {r.text[:200]}",
        )
    if r.status_code >= 400:
        raise BrokerError(
            f"TradeStation rejected token request ({r.status_code}): "
            f"{r.text[:200]}",
        )
    try:
        return r.json()
    except ValueError as exc:
        raise BrokerError(
            f"TradeStation token response was not JSON: {r.text[:200]}",
        ) from exc


def exchange_code_for_tokens(
    *, code: str, code_verifier: str, user,
) -> TokenBundle:
    client_id, client_secret, _src = resolve_app_credentials(user)
    if not client_id or not client_secret:
        raise BrokerError("TradeStation developer-app credentials are not configured")
    payload = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": settings.TRADESTATION_REDIRECT_URI,
    }
    data = _post(settings.TRADESTATION_TOKEN_URL, payload)
    return _bundle_from(data)


def _bundle_from(data: dict) -> TokenBundle:
    access = (data.get("access_token") or "").strip()
    if not access:
        raise BrokerError("TradeStation token response missing access_token")
    return TokenBundle(
        access_token=access,
        refresh_token=(data.get("refresh_token") or "").strip(),
        expires_in=int(data.get("expires_in") or 0),
        scopes=(data.get("scope") or "").strip(),
        raw=data,
    )


# --- refresh ----------------------------------------------------------------


def refresh_if_needed(account: BrokerAccount) -> str:
    """Return a live access token, refreshing if within REFRESH_MARGIN of
    expiry. Persists rotated tokens on the credential. Flips the account
    to `needs_reauth` and raises BrokerError on a hard refresh rejection.
    """
    try:
        cred: BrokerCredential = account.credential
    except BrokerCredential.DoesNotExist as exc:
        raise BrokerError(
            f"BrokerAccount {account.pk} has no OAuth credential",
        ) from exc
    if cred.auth_kind != BrokerCredential.AUTH_OAUTH2:
        raise BrokerError(
            f"BrokerAccount {account.pk} credential is not OAuth2 "
            f"(got {cred.auth_kind!r})",
        )
    expires_at = cred.token_expires_at
    if expires_at and expires_at - _now() > REFRESH_MARGIN:
        if cred.encrypted_access_token:
            return decrypt(cred.encrypted_access_token)
    # Refresh.
    if not cred.encrypted_refresh_token:
        _mark_needs_reauth(account)
        raise BrokerError("no refresh token on file — reconnect TradeStation")
    refresh_token = decrypt(cred.encrypted_refresh_token)
    client_id, client_secret, _src = resolve_app_credentials(account.user)
    if not client_id or not client_secret:
        _mark_needs_reauth(account)
        raise BrokerError(
            "TradeStation developer-app credentials are not configured — "
            "cannot refresh token",
        )
    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    try:
        data = _post(settings.TRADESTATION_TOKEN_URL, payload)
    except BrokerError as exc:
        # Hard rejection (4xx) means the refresh token is dead. Surface
        # needs_reauth so the gate refuses orders and the UI shows a
        # reconnect banner.
        _mark_needs_reauth(account)
        raise BrokerError(f"TradeStation refresh rejected: {exc}") from exc
    bundle = _bundle_from(data)
    # TradeStation may rotate the refresh token; if absent, keep the prior.
    persisted_refresh = bundle.refresh_token or refresh_token
    expires_at = _now() + timedelta(seconds=bundle.expires_in or 1200)
    set_oauth_tokens(
        account,
        access_token=bundle.access_token,
        refresh_token=persisted_refresh,
        expires_at=expires_at,
        scopes=bundle.scopes or cred.scopes,
        flavor=BrokerCredential.AUTH_OAUTH2,
    )
    return bundle.access_token


def _mark_needs_reauth(account: BrokerAccount) -> None:
    if account.connection_status != BrokerAccount.STATUS_NEEDS_REAUTH:
        BrokerAccount.objects.filter(pk=account.pk).update(
            connection_status=BrokerAccount.STATUS_NEEDS_REAUTH,
        )
        account.connection_status = BrokerAccount.STATUS_NEEDS_REAUTH
