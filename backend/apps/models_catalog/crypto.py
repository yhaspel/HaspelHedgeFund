"""Symmetric encryption for BYO provider keys and broker credentials.

Derives a Fernet cipher from a dedicated ``FIELD_ENCRYPTION_KEY`` (env), decoupled
from ``DJANGO_SECRET_KEY`` (ADR 0022). A ``MultiFernet`` keeps the legacy
``SECRET_KEY``-derived key valid for *decryption* during rollover, so rotating
``SECRET_KEY`` no longer silently bricks every stored key. New values are always
written with the primary (``FIELD_ENCRYPTION_KEY``) cipher.

``FIELD_ENCRYPTION_KEY`` itself rotates the same way: retired keys listed in
``FIELD_ENCRYPTION_KEY_PREVIOUS`` stay valid for *decryption* only, until
``manage.py reencrypt_vault --apply`` has moved every value to the primary.

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


def _keyring() -> list[tuple[str, Fernet]]:
    """Every key that may read a stored value, labelled, in ``MultiFernet`` order.

    The first entry is the write key, labelled "primary": ``FIELD_ENCRYPTION_KEY``,
    or the legacy key when that is unset. Then the decrypt-only
    ``FIELD_ENCRYPTION_KEY_PREVIOUS`` keys ("previous"), then the legacy
    ``SECRET_KEY``-derived key ("legacy").
    """
    primary = _primary_fernet()
    legacy = _legacy_fernet()
    raw = getattr(settings, "FIELD_ENCRYPTION_KEY_PREVIOUS", ()) or ()
    keys = raw.split(",") if isinstance(raw, str) else raw
    previous = [("previous", Fernet(key.strip().encode())) for key in keys if key.strip()]
    if primary is None:
        return [("primary", legacy), *previous]
    return [("primary", primary), *previous, ("legacy", legacy)]


def _cipher() -> MultiFernet:
    return MultiFernet([fernet for _, fernet in _keyring()])


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


def key_source(token: str) -> str:
    """Label of the key that reads ``token`` — "primary", "previous", "legacy" —
    or "undecryptable". For ``manage.py reencrypt_vault``; never logs."""
    for label, fernet in _keyring():
        try:
            fernet.decrypt(token.encode())
        except InvalidToken:
            continue
        return label
    return "undecryptable"


def reencrypt(token: str) -> str:
    """``token`` re-encrypted under the primary key (``MultiFernet.rotate``).

    Raises ``InvalidToken`` when no configured key reads it.
    """
    return _cipher().rotate(token.encode()).decode()
