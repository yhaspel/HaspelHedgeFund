from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rest_framework import serializers

from apps.models_catalog.presets import PRESETS
from apps.notifications.models import NotificationChannel
from apps.watchlists.models import Watchlist

from .models import ScheduledRun, ScheduledRunHistory
from .triggers import is_valid_cron


class ScheduledRunSerializer(serializers.ModelSerializer):
    watchlist = serializers.PrimaryKeyRelatedField(queryset=Watchlist.objects.all())
    watchlist_name = serializers.CharField(source="watchlist.name", read_only=True)
    notification_channel = serializers.PrimaryKeyRelatedField(
        queryset=NotificationChannel.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = ScheduledRun
        fields = (
            "id", "name", "watchlist", "watchlist_name", "personas",
            "model_preset", "model_overrides", "cron_expression", "timezone",
            "is_market_aware", "cost_ceiling_usd", "on_breach",
            "notification_channel", "is_active", "last_run_at", "next_run_at",
            "created_at", "updated_at",
        )
        read_only_fields = ("last_run_at", "next_run_at", "created_at", "updated_at")

    def _request_user(self):
        request = self.context.get("request")
        return request.user if request else None

    def validate_cron_expression(self, value: str) -> str:
        if not is_valid_cron(value):
            raise serializers.ValidationError(
                f"Invalid cron expression: {value!r} (expected 5 fields)."
            )
        return value

    def validate_timezone(self, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise serializers.ValidationError(f"Unknown timezone: {value!r}.") from exc
        return value

    def validate_model_preset(self, value: str) -> str:
        if value not in PRESETS:
            raise serializers.ValidationError(
                f"Unknown preset {value!r}. Choices: {sorted(PRESETS)}."
            )
        return value

    def validate_watchlist(self, wl: Watchlist) -> Watchlist:
        user = self._request_user()
        if user is not None and wl.user_id != user.id:
            raise serializers.ValidationError("Watchlist not found.")
        return wl

    def validate_notification_channel(self, ch):
        if ch is None:
            return ch
        user = self._request_user()
        if user is not None and ch.user_id != user.id:
            raise serializers.ValidationError("Notification channel not found.")
        return ch


class ScheduledRunHistorySerializer(serializers.ModelSerializer):
    run_ids = serializers.SerializerMethodField()

    class Meta:
        model = ScheduledRunHistory
        fields = (
            "id", "scheduled_run", "fire_time_utc", "started_at", "finished_at",
            "status", "estimated_cost_usd", "actual_cost_usd", "degraded_preset",
            "materiality_decision", "notified_count", "error", "run_ids",
        )

    def get_run_ids(self, obj: ScheduledRunHistory) -> list[int]:
        return list(obj.runs.values_list("id", flat=True))
