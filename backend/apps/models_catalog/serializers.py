from __future__ import annotations

from rest_framework import serializers

from .models import ModelEntry, ProviderKey, UserModelPreferences

# Tiers a USER may set a per-tier default for. Mirrors the frontend's
# USER_TIER_DEFAULT_PRESETS. Excludes 'hybrid', whose non-persona roles are
# intentionally local/Sonnet — a per-tier anchor would defeat that split.
USER_TIER_DEFAULT_PRESETS = ("dev", "frugal", "research", "quality")


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
        fields = (
            "preset", "per_agent_defaults", "per_tier_defaults",
            "cost_ceiling_per_run_usd",
        )

    def validate_per_tier_defaults(self, value):
        """Each {tier: model_id} must name a known tier and an ACTIVE model in
        that tier's menu (so a deactivated/off-menu pick can't be saved).
        `ollama:` ids are exempt (per-user, no catalog row)."""
        if not value:
            return value or {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("per_tier_defaults must be an object")
        from apps.models_catalog.tier_menus import tier_menu
        for tier, mid in value.items():
            if tier not in USER_TIER_DEFAULT_PRESETS:
                raise serializers.ValidationError(
                    f"per_tier_defaults tier {tier!r} is not user-settable "
                    f"(allowed: {list(USER_TIER_DEFAULT_PRESETS)})"
                )
            if str(mid).startswith("ollama:"):
                continue
            if mid not in set(tier_menu(tier)):
                raise serializers.ValidationError(
                    f"per_tier_defaults[{tier!r}] = {mid!r} is not an active "
                    f"model in that tier's menu"
                )
        return value


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
