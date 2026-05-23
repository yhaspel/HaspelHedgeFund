"""Encrypt / store / rotate / delete credential helpers.

Reuses the SECRET_KEY-derived Fernet pattern from
`apps/models_catalog/crypto.py` (P2d). This module is the **only** place
plaintext credentials are encrypted or decrypted; serializers must never
read the encrypted fields directly. P4a will replace this with a per-tenant
envelope vault.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.models_catalog.crypto import decrypt, encrypt

from .models import BrokerAccount, BrokerCredential


def set_api_key_secret(
    account: BrokerAccount,
    *,
    api_key: str,
    api_secret: str,
) -> BrokerCredential:
    """Store an API-key/secret pair on the account, replacing any prior creds."""
    if not api_key:
        raise ValueError("api_key is required")
    with transaction.atomic():
        cred, _ = BrokerCredential.objects.get_or_create(account=account)
        cred.auth_kind = BrokerCredential.AUTH_API_KEY
        cred.encrypted_api_key = encrypt(api_key)
        cred.encrypted_api_secret = encrypt(api_secret or "")
        cred.encrypted_access_token = ""
        cred.encrypted_refresh_token = ""
        cred.token_expires_at = None
        cred.rotated_at = timezone.now()
        cred.save()
    return cred


def set_oauth_tokens(
    account: BrokerAccount,
    *,
    access_token: str,
    refresh_token: str = "",
    expires_at=None,
    scopes: str = "",
    flavor: str = BrokerCredential.AUTH_OAUTH2,
) -> BrokerCredential:
    with transaction.atomic():
        cred, _ = BrokerCredential.objects.get_or_create(account=account)
        cred.auth_kind = flavor
        cred.encrypted_api_key = ""
        cred.encrypted_api_secret = ""
        cred.encrypted_access_token = encrypt(access_token)
        cred.encrypted_refresh_token = encrypt(refresh_token or "")
        cred.token_expires_at = expires_at
        cred.scopes = scopes or ""
        cred.rotated_at = timezone.now()
        cred.save()
    return cred


def zero_credential(account: BrokerAccount) -> None:
    """Idempotent: wipe credentials on disconnect. Keeps the row so audit FKs
    don't break, but every secret field is blanked."""
    try:
        cred = account.credential
    except BrokerCredential.DoesNotExist:
        return
    cred.encrypted_api_key = ""
    cred.encrypted_api_secret = ""
    cred.encrypted_access_token = ""
    cred.encrypted_refresh_token = ""
    cred.token_expires_at = None
    cred.rotated_at = timezone.now()
    cred.save()


def has_credential(account: BrokerAccount) -> bool:
    try:
        cred = account.credential
    except BrokerCredential.DoesNotExist:
        return False
    return cred.has_any_secret()


def with_credential(account: BrokerAccount, fn: Callable[[dict], Any]) -> Any:
    """Run `fn` with a dict of decrypted secrets scoped to the call.

    The decrypted dict is dropped before this function returns. Tests
    assert it doesn't outlive the call by passing a probe `fn` that closes
    over the dict and confirming subsequent reads are empty.
    """
    try:
        cred = account.credential
    except BrokerCredential.DoesNotExist:
        return fn({})
    secrets = {
        "api_key": decrypt(cred.encrypted_api_key) if cred.encrypted_api_key else "",
        "api_secret": (
            decrypt(cred.encrypted_api_secret) if cred.encrypted_api_secret else ""
        ),
        "access_token": (
            decrypt(cred.encrypted_access_token) if cred.encrypted_access_token else ""
        ),
        "refresh_token": (
            decrypt(cred.encrypted_refresh_token)
            if cred.encrypted_refresh_token else ""
        ),
    }
    try:
        return fn(secrets)
    finally:
        for k in list(secrets.keys()):
            secrets[k] = ""  # best-effort scrub
