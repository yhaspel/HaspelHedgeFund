"""ADR 0022 — FIELD_ENCRYPTION_KEY decoupling + MultiFernet rollover.

Locks the contract that:
  * new values are written under the dedicated FIELD_ENCRYPTION_KEY,
  * tokens written under the legacy SECRET_KEY-derived scheme still decrypt
    during rollover (MultiFernet secondary), and
  * an undecryptable token fails loudly (logged) but tolerantly (returns "").
"""
from __future__ import annotations

import logging

from cryptography.fernet import Fernet
from django.test import override_settings

from apps.models_catalog import crypto


def test_empty_roundtrip():
    assert crypto.encrypt("") == ""
    assert crypto.decrypt("") == ""


def test_rollover_legacy_token_still_decrypts():
    # Written under the pre-ADR-0022 scheme (no dedicated key).
    with override_settings(FIELD_ENCRYPTION_KEY=""):
        legacy_token = crypto.encrypt("sk-secret-value")

    new_key = Fernet.generate_key().decode()
    with override_settings(FIELD_ENCRYPTION_KEY=new_key):
        # MultiFernet secondary (legacy) still reads the old token.
        assert crypto.decrypt(legacy_token) == "sk-secret-value"
        # New writes use the primary (dedicated) key — a different ciphertext.
        new_token = crypto.encrypt("sk-secret-value")
        assert new_token != legacy_token
        assert crypto.decrypt(new_token) == "sk-secret-value"

    # The legacy key alone cannot read a token written under the dedicated key.
    with override_settings(FIELD_ENCRYPTION_KEY=""):
        assert crypto.decrypt(new_token) == ""


def test_decrypt_bad_token_is_loud(caplog):
    with caplog.at_level(logging.ERROR, logger="apps.models_catalog.crypto"):
        assert crypto.decrypt("not-a-valid-fernet-token") == ""
    assert any("decrypt_failed" in r.getMessage() for r in caplog.records)
