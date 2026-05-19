"""Symmetric encryption for BYO provider keys.

P2d-grade: derives a Fernet key from DJANGO_SECRET_KEY so we never store
plaintext keys in the DB. P4a will replace this with a proper multi-tenant
KMS envelope-encryption scheme.
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet() -> Fernet:
    secret = getattr(settings, "SECRET_KEY", "dev").encode()
    digest = hashlib.sha256(secret).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return ""
