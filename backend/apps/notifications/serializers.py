from __future__ import annotations

from rest_framework import serializers

from .models import NotificationChannel, NotificationEvent


class NotificationChannelSerializer(serializers.ModelSerializer):
    # ``config`` is write-only (it carries the Telegram bot_token); reads get a
    # redacted ``config_summary`` instead so secrets never leave the server.
    config = serializers.JSONField(write_only=True, required=False)
    config_summary = serializers.SerializerMethodField()
    # Whether a credential is on file, without revealing anything about it —
    # the flag the UI needs to render "Bot token: set / not set".
    has_token = serializers.SerializerMethodField()

    class Meta:
        model = NotificationChannel
        fields = (
            "id", "kind", "name", "is_active",
            "config", "config_summary", "has_token", "created_at", "updated_at",
        )
        read_only_fields = ("created_at", "updated_at")

    def get_has_token(self, obj: NotificationChannel) -> bool:
        return bool(obj.get_secret("bot_token"))

    def get_config_summary(self, obj: NotificationChannel) -> dict:
        cfg = obj.config or {}
        if obj.kind == NotificationChannel.TELEGRAM:
            token = obj.get_secret("bot_token")
            masked = ("…" + token[-4:]) if len(token) >= 4 else ("set" if token else "")
            return {"chat_id": cfg.get("chat_id", ""), "bot_token": masked}
        if obj.kind == NotificationChannel.EMAIL:
            return {"address": cfg.get("address", "")}
        return {}

    def validate(self, data: dict) -> dict:
        kind = data.get("kind", getattr(self.instance, "kind", None))
        # On create, config is required; on partial update it may be omitted.
        cfg = data.get("config")
        if cfg is None and self.instance is not None:
            return data
        cfg = cfg or {}
        if kind == NotificationChannel.TELEGRAM:
            if not cfg.get("bot_token") or not cfg.get("chat_id"):
                raise serializers.ValidationError(
                    "Telegram channels need a bot_token and chat_id "
                    "(see the Telegram setup guide)."
                )
        elif kind == NotificationChannel.EMAIL:
            # address optional — falls back to the user's account email.
            pass
        return data


class NotificationEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationEvent
        fields = (
            "id", "subject", "delivery_status", "error",
            "sent_at", "created_at",
        )
