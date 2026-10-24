"""Report encrypted-at-rest values whose ciphertext will not decrypt.

Diagnostic + verification tool for field-key rollovers (ADR 0022). Scans BYO
provider keys, broker credentials, per-user broker OAuth apps and the secret
``<key>_enc`` entries of notification-channel configs. A field is
"undecryptable" when it is non-empty but ``decrypt()`` returns "" — i.e. no
configured key (FIELD_ENCRYPTION_KEY, FIELD_ENCRYPTION_KEY_PREVIOUS, or the legacy
SECRET_KEY-derived key) can read it. Zero is the healthy state after the re-encrypt
migration (``0009_reencrypt_field_keys``) or ``manage.py reencrypt_vault --apply``.
Exit code is non-zero when any are found, so a wrapper/launchd job can detect it.

    python manage.py vault_doctor
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.models_catalog.crypto import decrypt

PROVIDER_KEY_FIELDS = [
    "anthropic_api_key_enc",
    "openrouter_api_key_enc",
    "openai_api_key_enc",
    "fmp_api_key_enc",
    "tiingo_api_key_enc",
    "fred_api_key_enc",
    "resend_api_key_enc",
]
BROKER_CRED_FIELDS = [
    "encrypted_api_key",
    "encrypted_api_secret",
    "encrypted_access_token",
    "encrypted_refresh_token",
]
OAUTH_APP_FIELDS = [
    "encrypted_client_id",
    "encrypted_client_secret",
]


def channel_secret_fields() -> list[str]:
    """The ``<key>_enc`` entries ``encrypt_config`` writes into a channel config."""
    from apps.notifications.models import SECRET_CONFIG_KEYS

    return [f"{key}_enc" for key in SECRET_CONFIG_KEYS]


class Command(BaseCommand):
    help = "Count encrypted-at-rest fields that fail to decrypt (ADR 0022)."

    def handle(self, *args, **opts) -> None:
        from apps.brokers.models import BrokerCredential, UserBrokerOAuthApp
        from apps.models_catalog.models import ProviderKey
        from apps.notifications.models import NotificationChannel

        checked = 0
        bad = 0

        def scan(rows, fields, label, id_attr, get=getattr):
            nonlocal checked, bad
            for row in rows:
                for field in fields:
                    token = get(row, field, "") or ""
                    if not token:
                        continue
                    checked += 1
                    if not decrypt(token):
                        bad += 1
                        rid = getattr(row, id_attr)
                        self.stderr.write(
                            f"[undecryptable] {label} {id_attr}={rid} field={field}"
                        )

        def config_get(row, key, default):
            return str((row.config or {}).get(key) or default)

        scan(ProviderKey.objects.all(), PROVIDER_KEY_FIELDS, "ProviderKey", "user_id")
        scan(BrokerCredential.objects.all(), BROKER_CRED_FIELDS, "BrokerCredential", "id")
        scan(UserBrokerOAuthApp.objects.all(), OAUTH_APP_FIELDS, "UserBrokerOAuthApp", "id")
        scan(
            NotificationChannel.objects.all(), channel_secret_fields(),
            "NotificationChannel.config", "id", get=config_get,
        )

        self.stdout.write(
            f"vault_doctor: {checked} encrypted field(s) checked, {bad} undecryptable."
        )
        if bad:
            raise CommandError(f"{bad} undecryptable field(s) — see rows above.")
