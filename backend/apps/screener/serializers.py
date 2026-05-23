"""Serializers for the market screener API.

The filter-set validator lives in ``pipeline.validate_filters`` so the
``/run/`` endpoint and the ``SavedScreen`` write serializer share the
same single source of truth.
"""
from __future__ import annotations

from typing import Any

from rest_framework import serializers

from .datasource import get_screener_datasource
from .fields import (
    CAPABILITY_LABEL,
    FIELD_REGISTRY,
    field_missing_capabilities,
    field_required_capabilities,
)
from .models import SavedScreen, WatchlistItem
from .pipeline import ScreenerValidationError, validate_filters


class ScreenerFieldSerializer(serializers.Serializer):
    id = serializers.CharField()
    label = serializers.CharField()
    group = serializers.CharField()
    kind = serializers.CharField()
    unit = serializers.CharField(allow_blank=True)
    asset_classes = serializers.ListField(child=serializers.CharField())
    enum_values = serializers.ListField(child=serializers.CharField())
    description = serializers.CharField(allow_blank=True)
    available = serializers.BooleanField()
    requires = serializers.ListField(child=serializers.CharField())


class PresetSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    asset_class = serializers.CharField()
    filters = serializers.DictField()
    sort = serializers.DictField()
    limit = serializers.IntegerField()
    available = serializers.BooleanField()
    requires = serializers.ListField(child=serializers.CharField())


class SavedScreenSerializer(serializers.ModelSerializer):
    class Meta:
        model = SavedScreen
        fields = (
            "id",
            "name",
            "asset_class",
            "filters",
            "sort",
            "based_on",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("created_at", "updated_at")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        user = self._user()
        name = attrs.get("name", "").strip()
        if not name:
            raise serializers.ValidationError({"name": "name is required"})
        attrs["name"] = name

        qs = SavedScreen.objects.filter(user=user, name__iexact=name)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                {"name": "a screen with this name already exists"}
            )

        full_filters: dict[str, Any] = {
            "asset_class": attrs.get(
                "asset_class", self.instance.asset_class if self.instance else "equity"
            ),
            "criteria": attrs.get("filters") or {},
            "sort": attrs.get("sort")
            or (self.instance.sort if self.instance else {"field": "market_cap", "dir": "desc"}),
            "limit": 200,
        }
        try:
            ds = get_screener_datasource(user)
            normalized = validate_filters(
                full_filters, available_capabilities=ds.capabilities()
            )
        except ScreenerValidationError as exc:
            raise serializers.ValidationError({"filters": str(exc)}) from exc
        except RuntimeError as exc:
            # No FMP key — still let the user save the screen; just skip
            # capability gating. The /run/ endpoint will surface the
            # actionable message at execution time.
            from .pipeline import validate_filters as _vf

            normalized = _vf(
                full_filters, available_capabilities=frozenset(CAPABILITY_LABEL.keys())
            )
            del exc

        attrs["filters"] = normalized["criteria"]
        attrs["sort"] = normalized["sort"]
        return attrs

    def _user(self):
        request = self.context.get("request")
        if request is None or not request.user or not request.user.is_authenticated:
            raise serializers.ValidationError("authentication required")
        return request.user


class WatchlistItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = WatchlistItem
        fields = ("id", "ticker", "note", "added_at")
        read_only_fields = ("added_at",)

    def validate_ticker(self, value: str) -> str:
        v = (value or "").strip().upper()
        if not v:
            raise serializers.ValidationError("ticker is required")
        if len(v) > 16:
            raise serializers.ValidationError("ticker is too long")
        return v


def serialize_fields(available_capabilities: frozenset) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in FIELD_REGISTRY.values():
        missing = field_missing_capabilities(f, available_capabilities)
        out.append(
            {
                "id": f.id,
                "label": f.label,
                "group": f.group,
                "kind": f.kind,
                "unit": f.unit,
                "asset_classes": list(f.asset_classes),
                "enum_values": list(f.enum_values),
                "description": f.description,
                "available": not missing,
                "requires": missing or field_required_capabilities(f),
            }
        )
    return out
