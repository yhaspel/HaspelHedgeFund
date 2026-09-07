"""Notification channels + delivery audit (P3b).

v1 ships **email** and **telegram**. ``kind`` is a CharField (not a hard FK to
a channel-type table) so adding Slack/Discord later is a one-line choices edit
plus a new ``channels/*.py`` sender.

``config`` is per-kind:
* email    → ``{"address": "you@example.com"}`` (falls back to ``user.email``)
* telegram → ``{"bot_token": "...", "chat_id": "..."}`` (user-supplied; see
  ``guides/telegram-setup.md``)

Secrets inside ``config`` are encrypted AT REST with the same Fernet helper
``ProviderKey`` uses (ADR 0022): a Telegram bot token is a full send-as-this-bot
credential, and it used to sit in plaintext JSON in a column that every DB
backup, read replica and ``dumpdata`` copies. Writes are normalised in
``save()`` — ``{"bot_token": x}`` is stored as ``{"bot_token_enc": <fernet>}`` —
so every creation path (API, admin, management command, fixture) is covered;
read it back with ``get_secret("bot_token")``.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.models_catalog.crypto import decrypt, encrypt

# config keys that hold a credential rather than an identifier.
SECRET_CONFIG_KEYS = ("bot_token",)


def encrypt_config(cfg: dict | None) -> dict:
    """Return ``cfg`` with every secret key replaced by its ``<key>_enc`` form.

    Idempotent: a config that is already encrypted round-trips unchanged, which
    is what makes both ``save()`` and the data migration safe to re-run.
    """
    out = dict(cfg or {})
    for key in SECRET_CONFIG_KEYS:
        raw = out.pop(key, None)
        if raw:
            out[f"{key}_enc"] = encrypt(str(raw))
    return out


class NotificationChannel(models.Model):
    EMAIL = "email"
    TELEGRAM = "telegram"
    KIND_CHOICES = [
        (EMAIL, "Email"),
        (TELEGRAM, "Telegram"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_channels",
    )
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    name = models.CharField(max_length=120, blank=True, default="")
    config = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} channel (user={self.user_id})"

    def save(self, *args, **kwargs):
        # Normalise on the way in so no caller can persist a plaintext secret,
        # wherever it came from (serializer, admin, shell, fixture).
        self.config = encrypt_config(self.config)
        return super().save(*args, **kwargs)

    @property
    def label(self) -> str:
        return self.name or self.get_kind_display()

    def get_secret(self, key: str) -> str:
        """Decrypted value of a secret config key ('' when unset).

        Falls back to a plaintext value for rows written before the encryption
        cutover that the data migration has not reached (a restored backup, a
        fixture loaded with ``loaddata --raw``).
        """
        cfg = self.config or {}
        enc = cfg.get(f"{key}_enc")
        if enc:
            return decrypt(str(enc))
        return str(cfg.get(key) or "")


class NotificationEvent(models.Model):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    THROTTLED = "throttled"
    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (SENT, "Sent"),
        (FAILED, "Failed"),
        (THROTTLED, "Throttled"),
    ]

    channel = models.ForeignKey(
        NotificationChannel, on_delete=models.CASCADE, related_name="events"
    )
    # Set when the event was produced by a scheduled-run fire. Nullable so test
    # messages (no schedule) and future ad-hoc notifications can reuse the model.
    triggered_by = models.ForeignKey(
        "schedules.ScheduledRunHistory",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="notification_events",
    )
    subject = models.CharField(max_length=255)
    body = models.TextField(blank=True, default="")
    # The ticker this event is about (blank for digests / test messages). Drives
    # the per-ticker daily throttle so a single noisy name can't dominate the cap.
    ticker = models.CharField(max_length=16, blank=True, default="")
    delivery_status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=PENDING
    )
    error = models.TextField(blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.subject} [{self.delivery_status}]"
