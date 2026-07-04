"""Re-encrypt BYO keys + broker credentials under FIELD_ENCRYPTION_KEY (ADR 0022).

Walks every ``ProviderKey`` and ``BrokerCredential`` row, decrypts each stored
field with the current cipher (``MultiFernet`` — new primary + legacy
``SECRET_KEY``-derived secondary) and re-encrypts with the primary. A field that
will not decrypt is left untouched (never overwritten with an empty re-encrypt)
so ``manage.py vault_doctor`` can surface it. Idempotent, and a no-op on installs
where ``FIELD_ENCRYPTION_KEY`` is unset (primary == legacy).
"""
from __future__ import annotations

from django.db import migrations

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


def _rotate(row, fields) -> None:
    from apps.models_catalog.crypto import decrypt, encrypt

    changed = []
    for field in fields:
        token = getattr(row, field, "") or ""
        if not token:
            continue
        plain = decrypt(token)
        if not plain:
            # Undecryptable — leave as-is; vault_doctor will report it. Never
            # clobber ciphertext with encrypt("") == "".
            continue
        setattr(row, field, encrypt(plain))
        changed.append(field)
    if changed:
        row.save(update_fields=changed)


def reencrypt(apps, schema_editor):
    ProviderKey = apps.get_model("models_catalog", "ProviderKey")
    BrokerCredential = apps.get_model("brokers", "BrokerCredential")
    for pk in ProviderKey.objects.all():
        _rotate(pk, PROVIDER_KEY_FIELDS)
    for cred in BrokerCredential.objects.all():
        _rotate(cred, BROKER_CRED_FIELDS)


class Migration(migrations.Migration):
    dependencies = [
        ("models_catalog", "0008_usermodelpreferences_per_tier_defaults"),
        ("brokers", "0006_p7_autonomous_fund"),
    ]

    operations = [
        migrations.RunPython(reencrypt, migrations.RunPython.noop),
    ]
