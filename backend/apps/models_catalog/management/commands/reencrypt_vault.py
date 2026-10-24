"""Re-encrypt every encrypted-at-rest value under the primary FIELD_ENCRYPTION_KEY.

Step two of a field-key rotation (procedure in .env.example): with the new key in
FIELD_ENCRYPTION_KEY and the old one in FIELD_ENCRYPTION_KEY_PREVIOUS everything
still decrypts; this moves the values written under a previous key, or under the
legacy SECRET_KEY-derived key, to the primary so the old key can be retired.

DRY RUN by default: per store/field, how many values the primary, a previous key
and the legacy key read, and how many no key reads. ``--apply`` re-encrypts every
readable non-primary value (``MultiFernet.rotate``) inside one transaction, with
a per-row ``QuerySet.update`` — never ``save()`` — so ``auto_now`` timestamps,
``BrokerCredential.rotated_at`` and model side effects are left alone; for a
notification channel only the ``<key>_enc`` entries of ``config`` change.
Idempotent: a second ``--apply`` re-encrypts nothing. Undecryptable values are
left untouched and counted (``vault_doctor`` lists the rows); the exit code is 0
unless ``--strict``. Never prints a value or a key.

    python manage.py reencrypt_vault            # dry run
    python manage.py reencrypt_vault --apply
"""
from __future__ import annotations

from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.models_catalog.crypto import key_source, reencrypt
from apps.models_catalog.management.commands.vault_doctor import (
    BROKER_CRED_FIELDS,
    OAUTH_APP_FIELDS,
    PROVIDER_KEY_FIELDS,
    channel_secret_fields,
)

SOURCES = ("primary", "previous", "legacy", "undecryptable")


class Command(BaseCommand):
    help = (
        "Re-encrypt encrypted-at-rest values under the primary FIELD_ENCRYPTION_KEY "
        "(dry run unless --apply)."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--apply", action="store_true",
            help="Write the re-encrypted values (default: dry run, nothing is written).",
        )
        parser.add_argument(
            "--strict", action="store_true",
            help="Exit non-zero when any undecryptable value remains.",
        )

    def handle(self, *args, **opts) -> None:
        from apps.brokers.models import BrokerCredential, UserBrokerOAuthApp
        from apps.models_catalog.models import ProviderKey
        from apps.notifications.models import NotificationChannel

        apply = opts["apply"]
        counts: dict[str, Counter] = {}

        def stale(name: str, token: str) -> bool:
            """Count ``token`` under ``name``; True when it needs re-encrypting."""
            source = key_source(token)
            counts[name][source] += 1
            return source in ("previous", "legacy")

        def rows(model, *fields):
            qs = model.objects.order_by("pk")
            if apply:
                # Hold each row from read to write so a concurrent edit is not lost.
                qs = qs.select_for_update()
            return qs.values_list("pk", *fields)

        with transaction.atomic():
            for model, fields in (
                (ProviderKey, PROVIDER_KEY_FIELDS),
                (BrokerCredential, BROKER_CRED_FIELDS),
                (UserBrokerOAuthApp, OAUTH_APP_FIELDS),
            ):
                for field in fields:
                    counts[f"{model.__name__}.{field}"] = Counter()
                for pk, *tokens in rows(model, *fields):
                    stale_tokens = {}
                    for field, token in zip(fields, tokens, strict=True):
                        if token and stale(f"{model.__name__}.{field}", token):
                            stale_tokens[field] = token
                    if apply and stale_tokens:
                        # update(), not save(): no auto_now bump, no rotated_at.
                        model.objects.filter(pk=pk).update(
                            **{field: reencrypt(token) for field, token in stale_tokens.items()}
                        )

            secret_keys = channel_secret_fields()
            for key in secret_keys:
                counts[f"NotificationChannel.config.{key}"] = Counter()
            for pk, config in rows(NotificationChannel, "config"):
                config = dict(config or {})
                stale_tokens = {}
                for key in secret_keys:
                    token = str(config.get(key) or "")
                    if token and stale(f"NotificationChannel.config.{key}", token):
                        stale_tokens[key] = token
                if apply and stale_tokens:
                    # Only the secret entries change; the rest of config is kept.
                    config.update({key: reencrypt(token) for key, token in stale_tokens.items()})
                    NotificationChannel.objects.filter(pk=pk).update(config=config)

        for name, by_source in counts.items():
            self.stdout.write(
                f"  {name}: " + " ".join(f"{s}={by_source[s]}" for s in SOURCES)
            )
        moved = sum(c["previous"] + c["legacy"] for c in counts.values())
        bad = sum(c["undecryptable"] for c in counts.values())
        if apply:
            self.stdout.write(
                f"reencrypt_vault: re-encrypted {moved} value(s) under the primary key; "
                f"{bad} undecryptable left untouched."
            )
        else:
            self.stdout.write(
                f"reencrypt_vault (dry run): {moved} value(s) to re-encrypt, "
                f"{bad} undecryptable. Nothing written; re-run with --apply."
            )
        if bad and opts["strict"]:
            raise CommandError(
                f"{bad} undecryptable value(s) remain — run vault_doctor to locate them."
            )
