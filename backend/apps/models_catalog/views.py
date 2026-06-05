from __future__ import annotations

from django.db import transaction
from rest_framework.permissions import IsAdminUser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from hedgefund_agents.registry import DEFAULT_MODELS

from .fetching import sync_tier_models
from .models import (
    ModelEntry,
    ProviderKey,
    TierConfig,
    TierMembership,
    UserModelPreferences,
)
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


def _tier_payload(tc: TierConfig) -> dict:
    members = tc.members.select_related("model").order_by("ordering", "model_id")
    return {
        "tier_name": tc.tier_name,
        "default_model": tc.default_model_id,
        "is_free_only": tc.is_free_only,
        "price_ceiling_in": (
            str(tc.price_ceiling_in) if tc.price_ceiling_in is not None else None
        ),
        "price_ceiling_out": (
            str(tc.price_ceiling_out) if tc.price_ceiling_out is not None else None
        ),
        "allow_reasoning": tc.allow_reasoning,
        "is_live_synced": tc.is_live_synced,
        "members": [
            {
                "model_id": m.model_id,
                "ordering": m.ordering,
                "role": m.role,
                "display_name": m.model.display_name,
                "supports_reasoning": m.model.supports_reasoning,
                "is_active": m.model.is_active,
            }
            for m in members
        ],
    }


def _validate_tier_members(tc: TierConfig, members: list[str]) -> str | None:
    """Operator edits get the SAME guards the curation philosophy + sync enforce:
    no reasoning model on a non-reasoning tier, dev free-only, frugal price
    ceiling. Returns an error string or None."""
    if not members:
        return None
    rows = {
        m.id: m
        for m in ModelEntry.objects.filter(id__in=members, is_active=True)
    }
    for mid in members:
        m = rows.get(mid)
        if m is None:
            return f"{mid!r} is not an active catalog model"
        if not tc.allow_reasoning and m.supports_reasoning:
            return (
                f"{mid!r} is a reasoning model; tier {tc.tier_name!r} does not "
                f"allow reasoning models"
            )
        if tc.is_free_only and not m.is_free:
            return f"{mid!r} is not free; tier {tc.tier_name!r} is free-only"
        if (
            tc.price_ceiling_in is not None
            and m.price_in_per_mtok is not None
            and m.price_in_per_mtok > tc.price_ceiling_in
        ) or (
            tc.price_ceiling_out is not None
            and m.price_out_per_mtok is not None
            and m.price_out_per_mtok > tc.price_ceiling_out
        ):
            return f"{mid!r} pricing exceeds tier {tc.tier_name!r} ceiling"
    return None


class TierConfigView(APIView):
    """Operator-only tier curation (membership + default per tier).

    GET  /api/tiers/          → all tiers.
    GET  /api/tiers/<name>/   → one tier.
    PUT  /api/tiers/<name>/   → replace one tier's members and/or default.

    Membership edits are guarded by the same policy the sync enforces (no
    reasoning on a non-reasoning tier, dev free-only, frugal ceiling) so an
    operator can't break the curation invariants. The 403 for non-staff is what
    the frontend uses to hide the editor.
    """

    permission_classes = [IsAdminUser]

    def get(self, request: Request, name: str | None = None) -> Response:
        if name:
            tc = TierConfig.objects.filter(tier_name=name).first()
            if tc is None:
                return Response({"detail": f"unknown tier {name!r}"}, status=404)
            return Response(_tier_payload(tc))
        return Response(
            {"tiers": [_tier_payload(tc) for tc in TierConfig.objects.all()]}
        )

    @transaction.atomic
    def put(self, request: Request, name: str | None = None) -> Response:
        if not name:
            return Response({"detail": "tier name required"}, status=400)
        tc = TierConfig.objects.filter(tier_name=name).first()
        if tc is None:
            return Response({"detail": f"unknown tier {name!r}"}, status=404)

        # Validate EVERYTHING before any write — a returned 4xx commits the
        # transaction (only a raise rolls back), so partial writes must be
        # impossible by construction.
        members = request.data.get("members", None)
        has_members = members is not None
        if has_members:
            if not isinstance(members, list):
                return Response(
                    {"detail": "members must be a list of model ids"}, status=400
                )
            err = _validate_tier_members(tc, members)
            if err:
                return Response({"detail": err}, status=400)

        set_default = "default_model" in request.data
        dm = request.data.get("default_model") if set_default else None
        if set_default and dm not in (None, ""):
            if not ModelEntry.objects.filter(id=dm, is_active=True).exists():
                return Response(
                    {"detail": f"default_model {dm!r} is not an active model"},
                    status=400,
                )
            # The default must belong to the FINAL member set (the new list if
            # provided, else the current membership).
            member_ids = set(members) if has_members else set(
                TierMembership.objects.filter(tier=tc).values_list("model_id", flat=True)
            )
            if dm not in member_ids:
                return Response(
                    {"detail": "default_model must be a member of the tier"},
                    status=400,
                )

        # --- all validation passed; apply ---
        if has_members:
            TierMembership.objects.filter(tier=tc).delete()
            for ordering, mid in enumerate(members):
                TierMembership.objects.create(tier=tc, model_id=mid, ordering=ordering)
        if set_default:
            tc.default_model_id = dm if dm not in (None, "") else None
            tc.save(update_fields=["default_model"])
        # A members-only edit can orphan the (unchanged) default; clear it if it
        # is no longer a member so the tier default is always a selectable member.
        if tc.default_model_id and not TierMembership.objects.filter(
            tier=tc, model_id=tc.default_model_id
        ).exists():
            tc.default_model = None
            tc.save(update_fields=["default_model"])

        tc.refresh_from_db()
        return Response(_tier_payload(tc))
