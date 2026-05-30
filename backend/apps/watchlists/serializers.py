from __future__ import annotations

from rest_framework import serializers

from .models import Watchlist, WatchlistTicker


class WatchlistTickerSerializer(serializers.ModelSerializer):
    class Meta:
        model = WatchlistTicker
        fields = ("id", "ticker", "note", "added_at")
        read_only_fields = ("added_at",)

    def validate_ticker(self, value: str) -> str:
        v = (value or "").strip().upper()
        if not v:
            raise serializers.ValidationError("ticker is required")
        if len(v) > 16:
            raise serializers.ValidationError("ticker is too long")
        return v


class WatchlistSerializer(serializers.ModelSerializer):
    ticker_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Watchlist
        fields = ("id", "name", "is_default", "ticker_count", "created_at", "updated_at")
        read_only_fields = ("is_default", "ticker_count", "created_at", "updated_at")

    def validate_name(self, value: str) -> str:
        v = (value or "").strip()
        if not v:
            raise serializers.ValidationError("name is required")
        if len(v) > 120:
            raise serializers.ValidationError("name is too long")
        return v
