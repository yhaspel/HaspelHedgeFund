from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from rest_framework import serializers

from .models import ModelEntry, ProviderKey, UserModelPreferences

# --- ollama_host SSRF guard --------------------------------------------------
# `ollama_host` is a user-supplied URL the SERVER fetches (GET <host>/api/tags on
# every /api/models/ and /api/presets/<name>/ read), which makes it a
# server-side request forgery primitive unless it is constrained. Ollama is a
# LAN/loopback daemon, so the allowed shape is deliberately narrow:
#
#   allowed  http(s)://localhost:11434, ://127.0.0.1, ://192.168.x.x,
#            ://10.x.x.x, ://172.16-31.x.x, ://ollama.home.lan
#   rejected any non-http(s) scheme (file:, gopher:, dict:, …), embedded
#            userinfo (`http://user:pw@host` — an easy parser-confusion trick),
#            the cloud metadata / link-local ranges 169.254.0.0/16 and fe80::/10,
#            a port outside 1-65535, and the well-known ports of the internal
#            services a compose/Railway deployment runs next to this app
#            (`http://redis:6379`, `http://db:5432`, …) — those are the pivot an
#            attacker actually wants, and no Ollama daemon listens there.
#
# Loopback and RFC1918 stay allowed on purpose: Ollama is a LAN daemon, and
# blocking private ranges would break the product's main configuration.
_ALLOWED_SCHEMES = ("http", "https")
_LINK_LOCAL_V4 = ipaddress.ip_network("169.254.0.0/16")
_LINK_LOCAL_V6 = ipaddress.ip_network("fe80::/10")
_BLOCKED_PORTS = frozenset({
    22, 23, 25, 53, 110, 143, 445, 465, 587, 993, 995,          # shell / mail / dns
    1433, 1521, 3306, 5432, 5984, 9042, 27017, 27018,           # databases
    6379, 6380, 11211,                                          # caches
    2181, 2375, 2376, 2379, 2380, 5672, 8500, 9200, 9300, 15672,  # infra / queues
})


def _validate_ollama_host(value: str) -> str:
    """Return ``value`` if it is a safe Ollama base URL, else raise.

    Blank means "unset" and is always allowed. A string ``urlsplit`` cannot
    parse at all (e.g. ``http://[::1``) is left alone: httpx raises
    ``InvalidURL`` on it, so it can never become an outbound request, and
    rejecting it here would not repair the rows already stored — the discovery
    path re-checks with this same function before it fetches anything.
    """
    host_url = (value or "").strip()
    if not host_url:
        return ""
    try:
        parts = urlsplit(host_url)
    except ValueError:
        return host_url  # unparseable ⇒ unfetchable; see docstring
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise serializers.ValidationError(
            f"ollama_host must be an http:// or https:// URL (got {host_url!r})."
        )
    if "@" in parts.netloc:
        raise serializers.ValidationError(
            "ollama_host must not contain credentials (an '@' in the host)."
        )
    try:
        hostname = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise serializers.ValidationError(f"ollama_host is not a valid URL: {exc}") from exc
    if not hostname:
        raise serializers.ValidationError(
            f"ollama_host must include a host name (got {host_url!r})."
        )
    if port is not None and not (1 <= port <= 65535):
        raise serializers.ValidationError(f"ollama_host port {port} is out of range.")
    if port in _BLOCKED_PORTS:
        raise serializers.ValidationError(
            f"Port {port} belongs to a well-known internal service (database, "
            "cache, queue, mail); an Ollama daemon does not listen there."
        )
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return host_url
    if ip in _LINK_LOCAL_V4 or ip in _LINK_LOCAL_V6 or ip.is_link_local:
        raise serializers.ValidationError(
            "ollama_host must not point at the link-local / cloud-metadata "
            "range (169.254.0.0/16, fe80::/10)."
        )
    return host_url


def is_safe_ollama_host(value: str) -> bool:
    """Non-raising form of :func:`_validate_ollama_host`, for the fetch path.

    Rows saved before the validator existed are still in the database, so
    discovery re-checks the host it is about to contact rather than trusting
    that it was validated on the way in.
    """
    try:
        return bool(_validate_ollama_host(value))
    except serializers.ValidationError:
        return False

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

    def validate_ollama_host(self, value):
        return _validate_ollama_host(value or "")
