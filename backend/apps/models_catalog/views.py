from __future__ import annotations

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from hedgefund_agents.registry import DEFAULT_MODELS

from .fetching import sync_tier_models
from .models import ModelEntry, ProviderKey, UserModelPreferences
from .ollama_discovery import discover_ollama_models
from .presets import ALL_AGENTS, PRESETS, expand_preset
from .serializers import (
    ModelEntrySerializer,
    ProviderKeyStatusSerializer,
    ProviderKeyWriteSerializer,
    UserModelPreferencesSerializer,
)
from .tier_menus import tier_menu
from .verification import verify_models

AGENT_RECOMMENDATIONS = {
    "buffett": "frontier", "munger": "frontier", "graham": "frontier",
    "wood": "frontier", "druckenmiller": "frontier", "burry": "frontier",
    "damodaran": "frontier", "lynch": "frontier",
    "fundamentals": "fast_cheap", "technicals": "fast_cheap",
    "valuation": "fast_cheap", "sentiment": "fast_cheap",
    "macro": "fast_cheap", "news_digest": "frontier",
    "risk_manager": "frontier", "portfolio_manager": "frontier", "cio": "frontier",
}


def _get_provider_keys(user) -> ProviderKey:
    pk, _ = ProviderKey.objects.get_or_create(user=user)
    return pk


class ModelsView(APIView):
    """GET /api/models/ — canonical catalog + discovered local models.

    Filters: when the user has not provided an API key for a provider, the
    provider's models are still listed but flagged `available=False`.
    """

    def get(self, request: Request) -> Response:
        pk = _get_provider_keys(request.user)
        catalog = list(ModelEntry.objects.filter(is_active=True))
        items = ModelEntrySerializer(catalog, many=True).data
        # discovered local
        items.extend(discover_ollama_models(pk.ollama_host))
        # mark availability
        from django.conf import settings as dj
        creds = {
            "anthropic": pk.has_key("anthropic") or bool(getattr(dj, "ANTHROPIC_API_KEY", "")),
            "openrouter": pk.has_key("openrouter") or bool(getattr(dj, "OPENROUTER_API_KEY", "")),
            "openai": pk.has_key("openai"),
            "ollama": bool(pk.ollama_host),
        }
        free_only = bool(getattr(dj, "LLM_FREE_ONLY", False))
        block_anthropic = bool(getattr(dj, "BLOCK_ANTHROPIC", False))
        for it in items:
            available = creds.get(it["provider"], False)
            # The runtime adapter refuses to init when BLOCK_ANTHROPIC is on,
            # so flag those rows unavailable up front rather than letting the
            # UI offer them and crash at run time.
            if block_anthropic and it["provider"] == "anthropic":
                available = False
            # In free-only mode (dev), every paid model is off-limits — even
            # OpenRouter routes that the platform key technically reaches.
            if free_only and not it.get("is_free"):
                available = False
            it["available"] = available
        return Response({"models": items})


class VerifyOpenRouterPricingView(APIView):
    """POST /api/models/verify-pricing/

    Body (optional): {"model_ids": ["openrouter:..."]}.
    Empty body verifies every active openrouter:* row. Returns the same
    payload shape as the management command, plus refreshed model rows so
    the UI can update last_verified_at/note without a second fetch.
    """

    def post(self, request: Request) -> Response:
        ids = request.data.get("model_ids") if isinstance(request.data, dict) else None
        try:
            results = verify_models(model_ids=ids or None)
        except Exception as e:
            return Response({"detail": f"verification failed: {e}"}, status=502)
        refreshed = ModelEntry.objects.filter(
            id__in=[r.model_id for r in results]
        )
        return Response({
            "results": [r.as_dict() for r in results],
            "models": ModelEntrySerializer(refreshed, many=True).data,
        })


AGENT_ORDER = [
    # Personas
    "buffett", "munger", "graham", "wood",
    "druckenmiller", "burry", "damodaran", "lynch",
    # Analyst agents
    "fundamentals", "technicals", "valuation", "sentiment",
    # Context agents
    "macro", "news_digest",
    # Orchestration
    "risk_manager", "portfolio_manager", "cio",
]

AGENT_GROUP = {
    **{a: "persona" for a in [
        "buffett", "munger", "graham", "wood",
        "druckenmiller", "burry", "damodaran", "lynch",
    ]},
    **{a: "analyst" for a in ["fundamentals", "technicals", "valuation", "sentiment"]},
    "macro": "context", "news_digest": "context",
    "risk_manager": "orchestration", "portfolio_manager": "orchestration", "cio": "orchestration",
}


class AgentsView(APIView):
    def get(self, request: Request) -> Response:
        agents = []
        seen = set()
        ordered = [a for a in AGENT_ORDER if a in ALL_AGENTS]
        # Any agents in ALL_AGENTS not in the explicit order get appended at the end.
        ordered += [a for a in sorted(ALL_AGENTS) if a not in ordered]
        for name in ordered:
            if name in seen:
                continue
            seen.add(name)
            default = DEFAULT_MODELS.get(name, ("anthropic", "claude-haiku-4-5-20251001"))
            agents.append({
                "id": name,
                "default_model": f"{default[0]}:{default[1]}",
                "recommended_tier": AGENT_RECOMMENDATIONS.get(name, "fast_cheap"),
                "group": AGENT_GROUP.get(name, "other"),
            })
        return Response({"agents": agents, "presets": list(PRESETS.keys())})


class PresetView(APIView):
    """GET /api/presets/<name>/ — returns the expanded per-agent map and
    the active tier's curated model menu (P3-C §6.6)."""

    def get(self, request: Request, name: str) -> Response:
        if name not in PRESETS:
            return Response({"detail": "unknown preset"}, status=404)
        pk = _get_provider_keys(request.user)
        local = discover_ollama_models(pk.ollama_host)
        # Prefer a local-A tier model for hybrid's analytical/macro/news,
        # falling back to the first discovered model.
        local_a = next(
            (m["id"] for m in local if (m.get("notes") or "").startswith("local-A")),
            None,
        ) or next((m["id"] for m in local), None)
        return Response({
            "preset": name,
            "overrides": expand_preset(name, local_a),
            "menu": tier_menu(name),
        })


class FetchOpenRouterModelsView(APIView):
    """POST /api/models/fetch/ — refresh dev+frugal ModelEntry rows from
    the live OpenRouter catalog.

    Body (optional): {"dry_run": true}. Returns the SyncResult shape plus
    refreshed ModelEntry rows so the UI can update without a second fetch.
    """

    def post(self, request: Request) -> Response:
        body = request.data if isinstance(request.data, dict) else {}
        dry_run = bool(body.get("dry_run", False))
        try:
            result = sync_tier_models(dry_run=dry_run)
        except Exception as e:
            return Response({"detail": f"fetch failed: {e}"}, status=502)
        refreshed = ModelEntry.objects.filter(
            id__in=[*result.synced, *result.deactivated]
        )
        payload = result.as_dict()
        payload["models"] = ModelEntrySerializer(refreshed, many=True).data
        return Response(payload)


class MyModelPreferencesView(APIView):
    def get(self, request: Request) -> Response:
        prefs, _ = UserModelPreferences.objects.get_or_create(user=request.user)
        return Response(UserModelPreferencesSerializer(prefs).data)

    def put(self, request: Request) -> Response:
        prefs, _ = UserModelPreferences.objects.get_or_create(user=request.user)
        ser = UserModelPreferencesSerializer(prefs, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)


class MyProviderKeysView(APIView):
    def get(self, request: Request) -> Response:
        pk = _get_provider_keys(request.user)
        return Response(ProviderKeyStatusSerializer(pk).data)

    def put(self, request: Request) -> Response:
        ser = ProviderKeyWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        pk = _get_provider_keys(request.user)
        data = ser.validated_data
        if "anthropic_api_key" in data:
            pk.set_key("anthropic", data["anthropic_api_key"])
        if "openrouter_api_key" in data:
            pk.set_key("openrouter", data["openrouter_api_key"])
        if "openai_api_key" in data:
            pk.set_key("openai", data["openai_api_key"])
        if "ollama_host" in data:
            pk.ollama_host = data["ollama_host"] or ""
        if "fmp_api_key" in data:
            pk.set_key("fmp", data["fmp_api_key"])
        if "tiingo_api_key" in data:
            pk.set_key("tiingo", data["tiingo_api_key"])
        if "fred_api_key" in data:
            pk.set_key("fred", data["fred_api_key"])
        if "resend_api_key" in data:
            pk.set_key("resend", data["resend_api_key"])
        pk.save()
        # P2n: clear factory caches so a freshly saved key takes effect on the
        # next provider call (the cache key includes api_key, so this is mostly
        # belt-and-suspenders, but a deliberate clear makes the test path crisp).
        from apps.data.providers.factory import _reset_caches_for_tests
        _reset_caches_for_tests()
        return Response(ProviderKeyStatusSerializer(pk).data)
