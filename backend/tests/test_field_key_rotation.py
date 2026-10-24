"""Field-key rotation — FIELD_ENCRYPTION_KEY_PREVIOUS + manage.py reencrypt_vault.

Locks the contract that:
  * a value written under a retired key still decrypts while that key is listed
    in FIELD_ENCRYPTION_KEY_PREVIOUS, and new writes always use the primary;
  * reencrypt_vault is a dry run by default; --apply moves every store
    (ProviderKey, BrokerCredential, UserBrokerOAuthApp, NotificationChannel
    config secrets) to the primary without touching timestamps, is idempotent,
    and leaves undecryptable values in place (exit 0 unless --strict);
  * a --strict dry run fails while any value is still to re-encrypt: the check
    before the old key is dropped, which vault_doctor cannot make;
  * vault_doctor scans the OAuth-app and notification-channel stores too, and
    the field lists both commands walk cover every encrypted column;
  * the non-dev boot guard rejects a malformed previous key without echoing it.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.brokers.credentials import set_api_key_secret
from apps.brokers.models import BrokerAccount, BrokerCredential, UserBrokerOAuthApp
from apps.models_catalog import crypto
from apps.models_catalog.management.commands.vault_doctor import (
    BROKER_CRED_FIELDS,
    OAUTH_APP_FIELDS,
    PROVIDER_KEY_FIELDS,
)
from apps.models_catalog.models import ProviderKey
from apps.notifications.models import NotificationChannel
from apps.portfolios.models import Portfolio

User = get_user_model()

OLD_KEY = Fernet.generate_key().decode()
NEW_KEY = Fernet.generate_key().decode()

# Mid-rotation (new primary, old key decrypt-only) and after it (old key gone).
ROTATING = {"FIELD_ENCRYPTION_KEY": NEW_KEY, "FIELD_ENCRYPTION_KEY_PREVIOUS": [OLD_KEY]}
NEW_ONLY = {"FIELD_ENCRYPTION_KEY": NEW_KEY, "FIELD_ENCRYPTION_KEY_PREVIOUS": []}

PLAIN = {
    "openrouter": "sk-or-v1-test",
    "fmp": "fmp-test-key",
    "api_key": "PK-TEST-KEY",
    "api_secret": "broker-secret-test",
    "client_id": "client-id-test",
    "client_secret": "client-secret-test",
    "bot_token": "123456:TEST-BOT-TOKEN",
}


@pytest.fixture
def vault(db):
    """One row in every encrypted-at-rest store, all written under OLD_KEY."""
    user = User.objects.create_user(email="rotate@x.test", password="x" * 12)
    with override_settings(FIELD_ENCRYPTION_KEY=OLD_KEY, FIELD_ENCRYPTION_KEY_PREVIOUS=[]):
        keys = ProviderKey.objects.create(user=user)
        keys.set_key("openrouter", PLAIN["openrouter"])
        keys.set_key("fmp", PLAIN["fmp"])
        keys.save()
        portfolio = Portfolio.objects.create(
            user=user, name="Broker · rotate", kind=Portfolio.KIND_BROKER,
        )
        account = BrokerAccount.objects.create(
            user=user, broker="demo", account_id="demo-rotate", label="rotate",
            portfolio=portfolio,
        )
        cred = set_api_key_secret(
            account, api_key=PLAIN["api_key"], api_secret=PLAIN["api_secret"],
        )
        app = UserBrokerOAuthApp.objects.create(
            user=user, broker="tradestation",
            encrypted_client_id=crypto.encrypt(PLAIN["client_id"]),
            encrypted_client_secret=crypto.encrypt(PLAIN["client_secret"]),
        )
        channel = NotificationChannel.objects.create(
            user=user, kind="telegram",
            config={"bot_token": PLAIN["bot_token"], "chat_id": "42"},
        )
    return SimpleNamespace(keys=keys, cred=cred, app=app, channel=channel)


def _raw(v) -> dict[str, str]:
    """The stored ciphertexts, re-read from the DB."""
    keys = ProviderKey.objects.get(pk=v.keys.pk)
    cred = BrokerCredential.objects.get(pk=v.cred.pk)
    app = UserBrokerOAuthApp.objects.get(pk=v.app.pk)
    channel = NotificationChannel.objects.get(pk=v.channel.pk)
    return {
        "openrouter": keys.openrouter_api_key_enc,
        "fmp": keys.fmp_api_key_enc,
        "api_key": cred.encrypted_api_key,
        "api_secret": cred.encrypted_api_secret,
        "client_id": app.encrypted_client_id,
        "client_secret": app.encrypted_client_secret,
        "bot_token": channel.config["bot_token_enc"],
    }


def _stamps(v) -> tuple:
    return (
        ProviderKey.objects.get(pk=v.keys.pk).updated_at,
        BrokerCredential.objects.get(pk=v.cred.pk).rotated_at,
        UserBrokerOAuthApp.objects.get(pk=v.app.pk).updated_at,
        NotificationChannel.objects.get(pk=v.channel.pk).updated_at,
    )


def _reencrypt(*args) -> str:
    out = io.StringIO()
    call_command("reencrypt_vault", *args, stdout=out)
    return out.getvalue()


def _assert_nothing_secret(text: str) -> None:
    for secret in (*PLAIN.values(), OLD_KEY, NEW_KEY):
        assert secret not in text


# --- crypto ------------------------------------------------------------------


def test_old_key_ciphertext_decrypts_while_listed_as_previous():
    with override_settings(FIELD_ENCRYPTION_KEY=OLD_KEY):
        old_token = crypto.encrypt("sk-or-v1-test")

    # Rotated without keeping the old key: unreadable (tolerant "", logged).
    with override_settings(**NEW_ONLY):
        assert crypto.decrypt(old_token) == ""

    with override_settings(**ROTATING):
        assert crypto.decrypt(old_token) == "sk-or-v1-test"
        assert crypto.key_source(old_token) == "previous"
        new_token = crypto.encrypt("sk-or-v1-test")
        assert crypto.key_source(new_token) == "primary"
        assert crypto.key_source("not-a-fernet-token") == "undecryptable"

    # Previous keys are decrypt-only: the new write is not under the old key.
    with override_settings(FIELD_ENCRYPTION_KEY=OLD_KEY, FIELD_ENCRYPTION_KEY_PREVIOUS=[]):
        assert crypto.decrypt(new_token) == ""


def test_previous_keys_accept_a_comma_separated_string():
    with override_settings(FIELD_ENCRYPTION_KEY=OLD_KEY):
        old_token = crypto.encrypt("fmp-test-key")
    other = Fernet.generate_key().decode()
    with override_settings(
        FIELD_ENCRYPTION_KEY=NEW_KEY, FIELD_ENCRYPTION_KEY_PREVIOUS=f" {other} , {OLD_KEY} ,",
    ):
        assert crypto.decrypt(old_token) == "fmp-test-key"


# --- reencrypt_vault ---------------------------------------------------------


def test_dry_run_reports_per_field_and_changes_nothing(vault):
    before, stamps = _raw(vault), _stamps(vault)
    with override_settings(**ROTATING):
        out = _reencrypt()

    assert _raw(vault) == before
    assert _stamps(vault) == stamps
    assert (
        "ProviderKey.openrouter_api_key_enc: primary=0 previous=1 legacy=0 undecryptable=0"
        in out
    )
    assert "ProviderKey.tiingo_api_key_enc: primary=0 previous=0 legacy=0 undecryptable=0" in out
    assert "BrokerCredential.encrypted_api_secret: primary=0 previous=1" in out
    assert "UserBrokerOAuthApp.encrypted_client_secret: primary=0 previous=1" in out
    assert "NotificationChannel.config.bot_token_enc: primary=0 previous=1" in out
    assert "(dry run): 7 value(s) to re-encrypt, 0 undecryptable" in out
    _assert_nothing_secret(out)


def test_apply_moves_every_store_to_the_primary_without_touching_timestamps(vault):
    stamps = _stamps(vault)
    with override_settings(**ROTATING):
        out = _reencrypt("--apply")
    assert "re-encrypted 7 value(s) under the primary key; 0 undecryptable" in out
    _assert_nothing_secret(out)

    # The old key can now be dropped: everything reads under the new key alone.
    with override_settings(**NEW_ONLY):
        raw = _raw(vault)
        assert {name: crypto.decrypt(token) for name, token in raw.items()} == PLAIN
        assert {crypto.key_source(token) for token in raw.values()} == {"primary"}
        channel = NotificationChannel.objects.get(pk=vault.channel.pk)
        assert channel.get_secret("bot_token") == PLAIN["bot_token"]

    # Only the secret entry of the channel config was rewritten.
    assert set(channel.config) == {"bot_token_enc", "chat_id"}
    assert channel.config["chat_id"] == "42"
    # QuerySet.update, not save(): auto_now and rotated_at are untouched.
    assert _stamps(vault) == stamps


def test_second_apply_is_a_noop(vault):
    with override_settings(**ROTATING):
        _reencrypt("--apply")
        after_first = _raw(vault)
        out = _reencrypt("--apply")
    assert "re-encrypted 0 value(s)" in out
    assert "ProviderKey.openrouter_api_key_enc: primary=1 previous=0" in out
    assert _raw(vault) == after_first


def test_undecryptable_values_are_left_untouched_and_counted(vault):
    lost = Fernet(Fernet.generate_key()).encrypt(b"lost-key-test").decode()
    ProviderKey.objects.filter(pk=vault.keys.pk).update(tiingo_api_key_enc=lost)

    with override_settings(**ROTATING):
        out = _reencrypt("--apply")  # exit 0 without --strict
        assert (
            "ProviderKey.tiingo_api_key_enc: primary=0 previous=0 legacy=0 undecryptable=1"
            in out
        )
        assert "re-encrypted 7 value(s) under the primary key; 1 undecryptable" in out
        assert ProviderKey.objects.get(pk=vault.keys.pk).tiingo_api_key_enc == lost

        with pytest.raises(CommandError, match="1 undecryptable value"):
            _reencrypt("--strict")
        with pytest.raises(CommandError, match="1 undecryptable value"):
            _reencrypt("--apply", "--strict")
    assert ProviderKey.objects.get(pk=vault.keys.pk).tiingo_api_key_enc == lost


def test_strict_dry_run_is_the_check_before_retiring_the_old_key(vault):
    with override_settings(**ROTATING):
        with pytest.raises(CommandError, match=r"7 value\(s\) still to re-encrypt"):
            _reencrypt("--strict")

        stale = ProviderKey.objects.get(pk=vault.keys.pk)  # loaded before the apply...
        _reencrypt("--apply")
        stale.set_key("tiingo", "tiingo-test-key")
        stale.save()  # ...and saved after it: its old-key ciphertexts are back

        # vault_doctor reads with every configured key, old one included, so it
        # cannot see this; the strict dry run can.
        call_command("vault_doctor", stdout=io.StringIO(), stderr=io.StringIO())
        with pytest.raises(CommandError, match=r"2 value\(s\) still to re-encrypt"):
            _reencrypt("--strict")

        _reencrypt("--apply")
        assert "(dry run): 0 value(s) to re-encrypt, 0 undecryptable" in _reencrypt("--strict")

    with override_settings(**NEW_ONLY):
        out = io.StringIO()
        call_command("vault_doctor", stdout=out, stderr=io.StringIO())
        assert "8 encrypted field(s) checked, 0 undecryptable." in out.getvalue()
        assert ProviderKey.objects.get(pk=vault.keys.pk).get_key("openrouter") == (
            PLAIN["openrouter"]
        )


@pytest.mark.django_db
def test_apply_moves_legacy_secret_key_values_to_the_primary():
    user = User.objects.create_user(email="legacy@x.test", password="x" * 12)
    with override_settings(FIELD_ENCRYPTION_KEY=""):  # pre-ADR-0022 scheme
        keys = ProviderKey.objects.create(user=user)
        keys.set_key("fred", "fred-test-key")
        keys.save()

    with override_settings(**NEW_ONLY):
        assert "ProviderKey.fred_api_key_enc: primary=0 previous=0 legacy=1" in _reencrypt()
        _reencrypt("--apply")
        token = ProviderKey.objects.get(pk=keys.pk).fred_api_key_enc
        assert crypto.key_source(token) == "primary"
        assert crypto.decrypt(token) == "fred-test-key"


# --- vault_doctor ------------------------------------------------------------


def test_scanned_field_lists_cover_every_encrypted_column():
    # Both commands walk these lists: a new encrypted column missing from them
    # would be neither re-encrypted nor checked.
    def columns(model, prefix="", suffix=""):
        return {
            f.name for f in model._meta.concrete_fields
            if f.name.startswith(prefix) and f.name.endswith(suffix)
        }

    assert set(PROVIDER_KEY_FIELDS) == columns(ProviderKey, suffix="_enc")
    assert set(BROKER_CRED_FIELDS) == columns(BrokerCredential, prefix="encrypted_")
    assert set(OAUTH_APP_FIELDS) == columns(UserBrokerOAuthApp, prefix="encrypted_")


def test_vault_doctor_scans_oauth_apps_and_channel_secrets(vault):
    # Old key dropped before re-encrypting: every store is unreadable.
    with override_settings(**NEW_ONLY):
        out, err = io.StringIO(), io.StringIO()
        with pytest.raises(CommandError, match="7 undecryptable"):
            call_command("vault_doctor", stdout=out, stderr=err)
    text = err.getvalue()
    app_id, channel_id = vault.app.pk, vault.channel.pk
    assert f"[undecryptable] UserBrokerOAuthApp id={app_id} field=encrypted_client_id" in text
    assert (
        f"[undecryptable] NotificationChannel.config id={channel_id} field=bot_token_enc"
        in text
    )
    _assert_nothing_secret(text + out.getvalue())

    with override_settings(**ROTATING):
        _reencrypt("--apply")
    with override_settings(**NEW_ONLY):
        out = io.StringIO()
        call_command("vault_doctor", stdout=out, stderr=io.StringIO())
    assert "vault_doctor: 7 encrypted field(s) checked, 0 undecryptable." in out.getvalue()


# --- settings boot guard -----------------------------------------------------

_SETTINGS_PROBE = (
    "import hedgefund.settings.base as base; "
    "print('PREVIOUS', len(base.FIELD_ENCRYPTION_KEY_PREVIOUS))"
)


def _boot_non_dev(previous: str) -> subprocess.CompletedProcess:
    """Import the base settings in a fresh interpreter as a prod-like env would."""
    env = {
        **os.environ,
        "DJANGO_ENV": "prod",
        "DJANGO_SECRET_KEY": "guard-test-secret-key-not-a-default-0123456789",
        "JWT_SIGNING_KEY": "guard-test-jwt-key-not-a-default-0123456789",
        "FIELD_ENCRYPTION_KEY": NEW_KEY,
        "FIELD_ENCRYPTION_KEY_PREVIOUS": previous,
    }
    env.pop("SENTRY_DSN", None)
    return subprocess.run(
        [sys.executable, "-c", _SETTINGS_PROBE],
        capture_output=True, text=True, env=env, cwd=settings.BASE_DIR,
    )


def test_boot_guard_rejects_a_bad_previous_key_without_echoing_it():
    result = _boot_non_dev(f"{OLD_KEY},not-a-fernet-key-GUARD-SENTINEL")
    assert result.returncode != 0
    assert "FIELD_ENCRYPTION_KEY_PREVIOUS entry 2 of 2 is not a valid Fernet key" in (
        result.stderr
    )
    output = result.stdout + result.stderr
    assert "GUARD-SENTINEL" not in output
    assert OLD_KEY not in output


def test_boot_guard_accepts_valid_previous_keys_with_whitespace():
    result = _boot_non_dev(f" {OLD_KEY} , {Fernet.generate_key().decode()} ,")
    assert result.returncode == 0, result.stderr
    assert "PREVIOUS 2" in result.stdout
