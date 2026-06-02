"""Starter graph templates + canonical-council JSON (P4c).

`create_canonical_council_version()` produces the JSON whose compiled graph is
structurally equivalent to `build_council_graph()` (the compiler-equivalence
test depends on it). `STARTERS` defines the platform templates seeded by the
data migration; `seed_templates()` is idempotent and `clone_into_user_graph()`
backs the "clone from template" endpoint.
"""
from __future__ import annotations

from hedgefund_agents.graphs.council import ANALYTICAL_NODES
from hedgefund_agents.personas import ALL_PERSONAS

from .registry import ALL_AGENTS
from .validators import TAIL_SELECTABLE, canonical_edges

# Lane layout constants for the initial canvas positions (the frontend may
# re-run auto-layout; these are sensible defaults).
_ANALYTICAL_X = 260
_PERSONA_X = 640
_Y0 = 80
_DY = 90


def _node(agent_name: str, x: int, y: int) -> dict:
    spec = ALL_AGENTS[agent_name]
    return {
        "id": agent_name,
        "type": agent_name,
        "kind": spec.kind,
        "position": {"x": x, "y": y},
        "model_id": spec.default_model_key,
        "config": {},
    }


def _tail_models() -> dict:
    return {t: ALL_AGENTS[t].default_model_key for t in TAIL_SELECTABLE}


def build_version_json(analytical: list[str], personas: list[str]) -> tuple[dict, dict]:
    """Return (graph_json, tail_models) for a given node selection."""
    nodes = [
        _node(name, _ANALYTICAL_X, _Y0 + i * _DY)
        for i, name in enumerate(analytical)
    ] + [
        _node(name, _PERSONA_X, _Y0 + i * _DY)
        for i, name in enumerate(personas)
    ]
    from .registry import SCHEMA_VERSION
    graph_json = {
        "schema_version": SCHEMA_VERSION,
        "nodes": nodes,
        "edges": canonical_edges(nodes),
    }
    return graph_json, _tail_models()


def create_canonical_council_version() -> tuple[dict, dict]:
    """The full council: all 6 analytical-tier agents + all 8 personas, in
    council.py order. Its compiled graph == build_council_graph()."""
    return build_version_json(list(ANALYTICAL_NODES.keys()), list(ALL_PERSONAS))


# Platform starter templates (D4). Each is seeded as an AgentGraph(is_template=
# True, user=None) with a single valid v1.
STARTERS: list[dict] = [
    {
        "name": "Council classic",
        "description": "The full agent council — every analytical-tier agent and "
                       "all eight investor personas. The default, mirrors the "
                       "hardcoded graph.",
        "analytical": list(ANALYTICAL_NODES.keys()),
        "personas": list(ALL_PERSONAS),
    },
    {
        "name": "Sector rotation",
        "description": "ETF-friendly fan-out — technicals, macro and news only "
                       "(no per-company fundamentals/valuation) — with the macro "
                       "trio of personas. Mirrors the sector-rotation builder.",
        "analytical": ["technicals", "macro", "news_digest"],
        "personas": ["druckenmiller", "damodaran", "burry"],
    },
    {
        "name": "Single persona (Buffett)",
        "description": "A lean starting point: fundamentals, technicals and "
                       "valuation feeding a single Buffett persona. Add personas "
                       "to taste.",
        "analytical": ["fundamentals", "technicals", "valuation"],
        "personas": ["buffett"],
    },
    {
        "name": "Value only",
        "description": "Value-investing slant — fundamentals, valuation and "
                       "sentiment feeding the value personas (Buffett, Munger, "
                       "Graham, Damodaran).",
        "analytical": ["fundamentals", "valuation", "sentiment"],
        "personas": ["buffett", "munger", "graham", "damodaran"],
    },
]


def seed_templates(apps=None) -> None:
    """Idempotently create the platform starter templates. Safe to call from a
    data migration (pass `apps` for the historical model) or at runtime.

    Keyed on (name, is_template=True, user=NULL); skips templates that already
    exist so re-running the migration never duplicates.
    """
    if apps is not None:
        AgentGraph = apps.get_model("graphs", "AgentGraph")
        AgentGraphVersion = apps.get_model("graphs", "AgentGraphVersion")
    else:
        from .models import AgentGraph, AgentGraphVersion

    for starter in STARTERS:
        graph, created = AgentGraph.objects.get_or_create(
            name=starter["name"],
            is_template=True,
            user=None,
            defaults={"description": starter["description"]},
        )
        if not created and graph.versions.exists():
            continue
        graph_json, tail_models = build_version_json(
            starter["analytical"], starter["personas"]
        )
        AgentGraphVersion.objects.create(
            graph=graph,
            version=1,
            nodes=graph_json["nodes"],
            edges=graph_json["edges"],
            tail_models=tail_models,
            validation_status="valid",
            notes="Seeded platform template.",
        )


def clone_into_user_graph(template, user, name: str):
    """Copy a template's latest version into a new user-owned graph (the
    "clone from template" endpoint). Fully independent — no back-reference."""
    from .models import AgentGraph, AgentGraphVersion

    src = template.versions.order_by("-version").first()
    graph = AgentGraph.objects.create(user=user, name=name, is_template=False,
                                      description=template.description)
    AgentGraphVersion.objects.create(
        graph=graph,
        version=1,
        nodes=(src.nodes if src else []),
        edges=(src.edges if src else []),
        tail_models=(src.tail_models if src else {}),
        validation_status=(src.validation_status if src else AgentGraphVersion.DRAFT_INVALID),
        created_by=user,
        notes=f"Cloned from template {template.name!r}.",
    )
    return graph
