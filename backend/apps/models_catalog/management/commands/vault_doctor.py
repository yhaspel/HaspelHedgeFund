"""Report BYO-key / broker-credential rows whose ciphertext will not decrypt.

Diagnostic + verification tool for the FIELD_ENCRYPTION_KEY rollover (ADR 0022).
A field is "undecryptable" when it is non-empty but ``decrypt()`` returns "" —
i.e. neither the primary (FIELD_ENCRYPTION_KEY) nor the legacy SECRET_KEY-derived
key can read it. Zero is the healthy state after the re-encrypt migration
(``0009_reencrypt_field_keys``). Exit code is non-zero when any are found, so a
wrapper/launchd job can detect it.

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


class Command(BaseCommand):
    help = "Count BYO-key / broker-credential rows that fail to decrypt (ADR 0022)."

    def handle(self, *args, **opts) -> None:
        from apps.brokers.models import BrokerCredential
        from apps.models_catalog.models import ProviderKey

        checked = 0
        bad = 0

        def scan(rows, fields, label, id_attr):
            nonlocal checked, bad
            for row in rows:
                for field in fields:
                    token = getattr(row, field, "") or ""
                    if not token:
                        continue
                    checked += 1
                    if not decrypt(token):
                        bad += 1
                        rid = getattr(row, id_attr)
                        self.stderr.write(
                            f"[undecryptable] {label} {id_attr}={rid} field={field}"
                        )

        scan(ProviderKey.objects.all(), PROVIDER_KEY_FIELDS, "ProviderKey", "user_id")
        scan(BrokerCredential.objects.all(), BROKER_CRED_FIELDS, "BrokerCredential", "id")

        self.stdout.write(
            f"vault_doctor: {checked} encrypted field(s) checked, {bad} undecryptable."
        )
        if bad:
            raise CommandError(f"{bad} undecryptable field(s) — see rows above.")
