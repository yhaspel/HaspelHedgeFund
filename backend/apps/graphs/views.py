"""Agent-graph editor API (P4c). All user-scoped: the visible queryset is
Q(user=request.user) | Q(is_template=True)."""
from __future__ import annotations

import logging

from django.db import IntegrityError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.models_catalog.presets import PRESETS
from apps.models_catalog.tier_menus import tier_default, tier_menu

from .models import AgentGraph, AgentGraphVersion
from .registry import ALL_AGENTS, SCHEMA_VERSION
from .serializers import (
    AgentGraphSerializer,
    AgentGraphVersionListSerializer,
    AgentGraphVersionSerializer,
    AgentGraphWriteSerializer,
    FromTemplateSerializer,
    VersionWriteSerializer,
)
from .templates import clone_into_user_graph
from .validators import fill_default_models, validate_graph

log = logging.getLogger(__name__)

PERSONA_LABELS = {
    "buffett": "Warren Buffett", "munger": "Charlie Munger", "graham": "Benjamin Graham",
    "wood": "Cathie Wood", "druckenmiller": "Stanley Druckenmiller", "burry": "Michael Burry",
    "damodaran": "Aswath Damodaran", "lynch": "Peter Lynch",
}
ANALYTICAL_LABELS = {
    "fundamentals": "Fundamentals", "technicals": "Technicals", "valuation": "Valuation",
    "sentiment": "Sentiment", "macro": "Macro", "news_digest": "News Digest",
}
TAIL_LABELS = {
    "risk_manager": "Risk Manager", "portfolio_manager": "Portfolio Manager", "cio": "CIO",
}
_LABELS = {**PERSONA_LABELS, **ANALYTICAL_LABELS, **TAIL_LABELS}


def _context_window_lookup(model_id: str) -> int | None:
    from apps.models_catalog.models import ModelEntry

    return (
        ModelEntry.objects.filter(id=model_id)
        .values_list("context_window", flat=True)
        .first()
    )


def _visible_graphs(user):
    return AgentGraph.objects.filter(Q(user=user) | Q(is_template=True))


def _node_meta(name: str) -> dict:
    s = ALL_AGENTS[name]
    return {
        "agent_name": s.agent_name,
        "kind": s.kind,
        "state_key": s.state_key,
        "model_selectable": s.model_selectable,
        "editable": s.editable,
        "default_model_key": s.default_model_key,
        "label": _LABELS.get(name, name.replace("_", " ").title()),
    }


# Price tiers = the model presets (Dev/Frugal/Hybrid/Research/Quality), cheap→premium.
_TIER_ORDER = ["dev", "frugal", "hybrid", "research", "quality"]
_TIER_LABELS = {
    "dev": "Dev (free)", "frugal": "Frugal", "hybrid": "Hybrid",
    "research": "Research", "quality": "Quality",
}


def _tiers_payload() -> list[dict]:
    """For the editor's bulk tier switcher: each tier's curated model menu +
    the default model applied to all nodes when the tier is picked. Both come
    from the DB-backed tier config (active-only membership; the operator-set /
    seeded TierConfig.default_model), via tier_menus.tier_menu/tier_default."""
    out = []
    for name in _TIER_ORDER:
        if name not in PRESETS:
            continue
        out.append({
            "name": name,
            "label": _TIER_LABELS.get(name, name.title()),
            "default_model": tier_default(name),
            "models": tier_menu(name),
        })
    return out


class GraphRegistryView(APIView):
    """GET /api/graphs/registry/ — palette + locked-tail metadata for the editor.

    The macro inspector caption (best-effort per-date cache) and the
    news_digest min-context note are surfaced so the client doesn't hardcode them.
    """

    def get(self, request: Request) -> Response:
        analytical = [
            "fundamentals", "technicals", "valuation", "sentiment", "macro", "news_digest",
        ]
        personas = [
            "buffett", "munger", "graham", "wood",
            "druckenmiller", "burry", "damodaran", "lynch",
        ]
        tail = ["risk_manager", "portfolio_manager", "cio"]
        return Response({
            "schema_version": SCHEMA_VERSION,
            "analytical": [_node_meta(n) for n in analytical],
            "personas": [_node_meta(n) for n in personas],
            "tail": [_node_meta(n) for n in tail],
            "structural": ["entry", "analytical_join", "persona_join"],
            "tiers": _tiers_payload(),
            "notes": {
                "macro": "Shared per-date cache: this model applies on the first run "
                         "for a date; later runs reuse the cached narrative.",
                "news_digest_min_context": 32_000,
            },
        })


class GraphListCreateView(generics.ListCreateAPIView):
    def get_queryset(self):
        return (
            _visible_graphs(self.request.user)
            .filter(archived_at__isnull=True)
            .prefetch_related("versions")
        )

    def get_serializer_class(self):
        return AgentGraphWriteSerializer if self.request.method == "POST" else AgentGraphSerializer

    def create(self, request, *args, **kwargs):
        ser = AgentGraphWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            graph = ser.save(user=request.user, is_template=False)
        except IntegrityError:
            return Response(
                {"name": ["You already have a graph with this name."]},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(AgentGraphSerializer(graph, context={"request": request}).data,
                        status=status.HTTP_201_CREATED)


class GraphDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AgentGraphSerializer

    def get_queryset(self):
        return _visible_graphs(self.request.user)

    def _ensure_owned(self, graph: AgentGraph) -> Response | None:
        if graph.is_template or graph.user_id != self.request.user.id:
            return Response({"detail": "Templates and other users' graphs are read-only."},
                            status=status.HTTP_403_FORBIDDEN)
        return None

    def update(self, request, *args, **kwargs):
        graph = self.get_object()
        denied = self._ensure_owned(graph)
        if denied:
            return denied
        ser = AgentGraphWriteSerializer(graph, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        try:
            ser.save()
        except IntegrityError:
            return Response({"name": ["You already have a graph with this name."]},
                            status=status.HTTP_409_CONFLICT)
        return Response(AgentGraphSerializer(graph, context={"request": request}).data)

    def destroy(self, request, *args, **kwargs):
        # Soft-archive: versions may be PROTECT-referenced by historical runs,
        # so a hard delete is unsafe. Archiving hides the graph from the list.
        graph = self.get_object()
        denied = self._ensure_owned(graph)
        if denied:
            return denied
        graph.archived_at = timezone.now()
        graph.save(update_fields=["archived_at", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class GraphVersionListCreateView(APIView):
    """GET version history; POST a new immutable version (validated)."""

    def _graph(self, request, graph_id):
        return get_object_or_404(_visible_graphs(request.user), pk=graph_id)

    def get(self, request: Request, graph_id: int) -> Response:
        graph = self._graph(request, graph_id)
        versions = graph.versions.all()
        return Response(AgentGraphVersionListSerializer(versions, many=True).data)

    def post(self, request: Request, graph_id: int) -> Response:
        graph = self._graph(request, graph_id)
        if graph.is_template or graph.user_id != request.user.id:
            return Response(
                {"detail": "Cannot add versions to a template or another user's graph."},
                status=status.HTTP_403_FORBIDDEN,
            )
        inp = VersionWriteSerializer(data=request.data)
        inp.is_valid(raise_exception=True)
        nodes, tail_models = fill_default_models(
            inp.validated_data["nodes"], inp.validated_data["tail_models"]
        )
        edges = inp.validated_data["edges"]
        result = validate_graph(nodes, edges, tail_models,
                                context_window_lookup=_context_window_lookup)
        if not result.is_valid:
            return Response(result.to_dict(), status=status.HTTP_400_BAD_REQUEST)

        version = AgentGraphVersion.objects.create(
            graph=graph,
            version=graph.next_version_number(),
            nodes=nodes,
            edges=edges,
            tail_models=tail_models,
            validation_status=AgentGraphVersion.VALID,
            created_by=request.user,
            notes=inp.validated_data.get("notes", ""),
        )
        graph.save(update_fields=["updated_at"])  # bump for list ordering
        body = AgentGraphVersionSerializer(version).data
        body["validation"] = result.to_dict()
        return Response(body, status=status.HTTP_201_CREATED)


class GraphVersionDetailView(APIView):
    def get(self, request: Request, graph_id: int, version: int) -> Response:
        graph = get_object_or_404(_visible_graphs(request.user), pk=graph_id)
        v = get_object_or_404(graph.versions, version=version)
        return Response(AgentGraphVersionSerializer(v).data)


class GraphValidateView(APIView):
    """POST /api/graphs/validate/ — validate a draft without saving."""

    def post(self, request: Request) -> Response:
        inp = VersionWriteSerializer(data=request.data)
        inp.is_valid(raise_exception=True)
        nodes, tail_models = fill_default_models(
            inp.validated_data["nodes"], inp.validated_data["tail_models"]
        )
        result = validate_graph(nodes, inp.validated_data["edges"], tail_models,
                                context_window_lookup=_context_window_lookup)
        return Response(result.to_dict())


class GraphFromTemplateView(APIView):
    """POST /api/graphs/from-template/<template_id>/ — clone a starter."""

    def post(self, request: Request, template_id: int) -> Response:
        template = get_object_or_404(AgentGraph, pk=template_id, is_template=True)
        ser = FromTemplateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            graph = clone_into_user_graph(template, request.user, ser.validated_data["name"])
        except IntegrityError:
            return Response({"name": ["You already have a graph with this name."]},
                            status=status.HTTP_409_CONFLICT)
        return Response(AgentGraphSerializer(graph, context={"request": request}).data,
                        status=status.HTTP_201_CREATED)
