from __future__ import annotations

from rest_framework import serializers

from .models import ModelEntry, ProviderKey, UserModelPreferences


class ModelEntrySerializer(serializers.ModelSerializer):
    is_free = serializers.BooleanField(read_only=True)

    class Meta:
        model = ModelEntry
        fields = (
            "id", "provider", "display_name", "tier",
            "context_window", "supports_caching", "supports_structured_output",
            "supports_long_context", "supports_reasoning",
            "price_in_per_mtok", "price_out_per_mtok",
            "notes", "is_free", "last_verified_at", "last_verified_note",
        )


class UserModelPreferencesSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserModelPreferences
        fields = ("preset", "per_agent_defaults", "cost_ceiling_per_run_usd")


class ProviderKeyStatusSerializer(serializers.Serializer):
    """Read-side: never leaks the actual key. Only 'set' / 'unset'."""

    anthropic = serializers.SerializerMethodField()
    openrouter = serializers.SerializerMethodField()
    openai = serializers.SerializerMethodField()
    ollama_host = serializers.CharField(allow_blank=True)
    fmp = serializers.SerializerMethodField()
    tiingo = serializers.SerializerMethodField()
    fred = serializers.SerializerMethodField()
    resend = serializers.SerializerMethodField()

    def get_anthropic(self, obj: ProviderKey) -> str:
        return "set" if obj.has_key("anthropic") else "unset"

    def get_openrouter(self, obj: ProviderKey) -> str:
        return "set" if obj.has_key("openrouter") else "unset"

    def get_openai(self, obj: ProviderKey) -> str:
        return "set" if obj.has_key("openai") else "unset"

    def get_fmp(self, obj: ProviderKey) -> str:
        return "set" if obj.has_key("fmp") else "unset"

    def get_tiingo(self, obj: ProviderKey) -> str:
        return "set" if obj.has_key("tiingo") else "unset"

    def get_fred(self, obj: ProviderKey) -> str:
        return "set" if obj.has_key("fred") else "unset"

    def get_resend(self, obj: ProviderKey) -> str:
        return "set" if obj.has_key("resend") else "unset"


class ProviderKeyWriteSerializer(serializers.Serializer):
    anthropic_api_key = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    openrouter_api_key = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    openai_api_key = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    ollama_host = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    fmp_api_key = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    tiingo_api_key = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    fred_api_key = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    resend_api_key = serializers.CharField(required=False, allow_blank=True, allow_null=True)
