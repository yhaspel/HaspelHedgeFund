"""Symmetric encryption for BYO provider keys and broker credentials.

Derives a Fernet cipher from a dedicated ``FIELD_ENCRYPTION_KEY`` (env), decoupled
from ``DJANGO_SECRET_KEY`` (ADR 0022). A ``MultiFernet`` keeps the legacy
``SECRET_KEY``-derived key valid for *decryption* during rollover, so rotating
``SECRET_KEY`` no longer silently bricks every stored key. New values are always
written with the primary (``FIELD_ENCRYPTION_KEY``) cipher.

When ``FIELD_ENCRYPTION_KEY`` is unset (dev convenience), the legacy
``SECRET_KEY``-derived key is the primary — existing dev installs keep working
with no ``.env`` change, and the non-dev boot guard in ``settings/base.py``
refuses to start without a real value.
"""
from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings

log = logging.getLogger(__name__)


def _legacy_fernet() -> Fernet:
    """Fernet derived from ``DJANGO_SECRET_KEY`` — the pre-ADR-0022 scheme.

    Kept as a ``MultiFernet`` secondary so rows written before the
    ``FIELD_ENCRYPTION_KEY`` cutover still decrypt during rollover.
    """
    secret = getattr(settings, "SECRET_KEY", "dev").encode()
    digest = hashlib.sha256(secret).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _primary_fernet() -> Fernet | None:
    """Fernet from the dedicated ``FIELD_ENCRYPTION_KEY``, or ``None`` when unset.

    The value is a urlsafe-base64 32-byte Fernet key (generate with
    ``python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"``).
    """
    key = (getattr(settings, "FIELD_ENCRYPTION_KEY", "") or "").strip()
    if not key:
        return None
    return Fernet(key.encode())


def _cipher() -> MultiFernet:
    primary = _primary_fernet()
    legacy = _legacy_fernet()
    keys = [primary, legacy] if primary is not None else [legacy]
    return MultiFernet(keys)


def encrypt(value: str) -> str:
    if not value:
        return ""
    return _cipher().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _cipher().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        # Tolerant by contract (callers read "" as "no key configured"), but a
        # non-empty token that will not decrypt is corruption or a key mismatch,
        # not an absent key — surface it loudly. Never log the token or the key.
        log.error(
            "decrypt_failed: stored ciphertext could not be decrypted "
            "(SECRET_KEY/FIELD_ENCRYPTION_KEY rotated without re-encrypt, or a "
            "corrupted row). Run `manage.py vault_doctor` to locate the rows."
        )
        return ""
