"""Encrypt Telegram bot tokens already stored in NotificationChannel.config.

Data-only (``config`` is a JSONField, so there is no schema change): every row
holding a plaintext ``bot_token`` is rewritten as ``bot_token_enc`` with the
same Fernet helper ``ProviderKey`` uses. Idempotent in both directions — a row
that is already encrypted has no ``bot_token`` key and is skipped, so a re-run
(or a partially-applied migration) is a no-op.

The reverse operation is deliberately a decrypt, not a data loss: rolling back
past this migration must leave a working install.
"""
from __future__ import annotations

from django.db import migrations


def encrypt_tokens(apps, schema_editor):
    from apps.models_catalog.crypto import encrypt

    Channel = apps.get_model("notifications", "NotificationChannel")
    for ch in Channel.objects.exclude(config={}).iterator():
        cfg = dict(ch.config or {})
        raw = cfg.pop("bot_token", None)
        if not raw:
            continue
        cfg["bot_token_enc"] = encrypt(str(raw))
        ch.config = cfg
        ch.save(update_fields=["config"])


def decrypt_tokens(apps, schema_editor):
    from apps.models_catalog.crypto import decrypt

    Channel = apps.get_model("notifications", "NotificationChannel")
    for ch in Channel.objects.exclude(config={}).iterator():
        cfg = dict(ch.config or {})
        enc = cfg.pop("bot_token_enc", None)
        if not enc:
            continue
        cfg["bot_token"] = decrypt(str(enc))
        ch.config = cfg
        ch.save(update_fields=["config"])


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0003_notificationevent_ticker"),
    ]

    operations = [
        migrations.RunPython(encrypt_tokens, decrypt_tokens),
    ]
