"""DRF serializers for the agent-graph editor API (P4c)."""
from __future__ import annotations

from rest_framework import serializers

from .models import AgentGraph, AgentGraphVersion


class AgentGraphVersionSerializer(serializers.ModelSerializer):
    display_label = serializers.CharField(read_only=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = AgentGraphVersion
        fields = (
            "id", "graph", "version", "nodes", "edges", "tail_models",
            "validation_status", "created_at", "created_by", "notes",
            "display_label",
        )
        read_only_fields = fields


class AgentGraphVersionListSerializer(serializers.ModelSerializer):
    """Light row for the version-history list (no node/edge payload)."""
    display_label = serializers.CharField(read_only=True)

    class Meta:
        model = AgentGraphVersion
        fields = ("id", "version", "validation_status", "created_at", "notes",
                  "display_label")
        read_only_fields = fields


class AgentGraphSerializer(serializers.ModelSerializer):
    """Read serializer for list/detail. `owned` lets the client gate edit
    affordances (templates are read-only)."""
    owned = serializers.SerializerMethodField()
    latest_version = serializers.SerializerMethodField()
    version_count = serializers.SerializerMethodField()

    class Meta:
        model = AgentGraph
        fields = (
            "id", "name", "description", "is_template", "owned",
            "created_at", "updated_at", "archived_at",
            "latest_version", "version_count",
        )
        read_only_fields = fields

    def get_owned(self, obj: AgentGraph) -> bool:
        request = self.context.get("request")
        return bool(request and obj.user_id == getattr(request.user, "id", None))

    def get_version_count(self, obj: AgentGraph) -> int:
        return obj.versions.count()

    def get_latest_version(self, obj: AgentGraph) -> dict | None:
        v = obj.versions.order_by("-version").first()
        return AgentGraphVersionListSerializer(v).data if v else None


class AgentGraphWriteSerializer(serializers.ModelSerializer):
    """Create / rename / archive (own graphs only)."""

    class Meta:
        model = AgentGraph
        fields = ("id", "name", "description", "archived_at")
        read_only_fields = ("id",)

    def validate_name(self, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("name is required")
        return value


class VersionWriteSerializer(serializers.Serializer):
    """Input parsing for POST /graphs/<id>/versions/ and /graphs/validate/."""
    nodes = serializers.ListField(child=serializers.DictField(), default=list)
    edges = serializers.ListField(child=serializers.DictField(), default=list)
    tail_models = serializers.DictField(default=dict)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class FromTemplateSerializer(serializers.Serializer):
    name = serializers.CharField()

    def validate_name(self, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("name is required")
        return value
