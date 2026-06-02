"""Authoritative server-side validation for saved graph versions (P4c).

The client mirrors these rules for live feedback, but THIS is the source of
truth: `POST /api/graphs/<id>/versions/` refuses to mark a version "valid" if
any Error fires, and (with BLOCK_INVALID_GRAPH_AT_SUBMISSION) a run cannot
select a non-valid version.

The runtime topology is fully determined by the node SET (which analytical-tier
agents + which personas are present): the compiler derives the canonical wiring
(entry → analytical → analytical_join → persona → persona_join → fixed tail)
from node kinds, exactly like `build_council_graph`. So edge validation here
checks the canvas representation is well-formed — the runtime is safe regardless.

No Django imports at module load: the ModelEntry context-window lookup is
injected, so this module is unit-testable in isolation.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .registry import (
    ALL_AGENTS,
    ANALYTICAL_TYPES,
    EDITABLE_TYPES,
    PERSONA_TYPES,
    STRUCTURAL_TYPES,
    TAIL_TYPES,
)

ENTRY = "entry"
ANALYTICAL_JOIN = "analytical_join"
PERSONA_JOIN = "persona_join"

# Tail nodes that carry a user-selectable model (stored in version.tail_models).
TAIL_SELECTABLE = frozenset(n for n in TAIL_TYPES if ALL_AGENTS[n].model_selectable)

NEWS_DIGEST_MIN_CONTEXT = 32_000


@dataclass(frozen=True)
class Issue:
    rule: str
    severity: str  # "error" | "warning"
    message: str
    node_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "node_id": self.node_id,
        }


@dataclass
class ValidationResult:
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    @property
    def status(self) -> str:
        from .models import AgentGraphVersion

        return AgentGraphVersion.VALID if self.is_valid else AgentGraphVersion.DRAFT_INVALID

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "is_valid": self.is_valid,
            "errors": [i.to_dict() for i in self.errors],
            "warnings": [i.to_dict() for i in self.warnings],
        }


def canonical_edges(nodes: list[dict]) -> list[dict]:
    """The deterministic wiring for a node set — what the compiler builds and
    what a well-formed canvas stores. Analytical fan-out from `entry` into
    `analytical_join`; persona fan-out from `analytical_join` into
    `persona_join`. Uses each node's `id` (which must equal its `type`)."""
    edges: list[dict] = []
    for n in nodes:
        nid = n.get("id")
        ntype = n.get("type")
        if ntype in ANALYTICAL_TYPES:
            edges.append({"from": ENTRY, "to": nid})
            edges.append({"from": nid, "to": ANALYTICAL_JOIN})
        elif ntype in PERSONA_TYPES:
            edges.append({"from": ANALYTICAL_JOIN, "to": nid})
            edges.append({"from": nid, "to": PERSONA_JOIN})
    return edges


def _edge_pair(e: dict) -> tuple:
    return (e.get("from"), e.get("to"))


def _has_cycle(edges: set[tuple]) -> bool:
    adj: dict[str, list[str]] = {}
    for f, t in edges:
        adj.setdefault(f, []).append(t)
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {}

    def visit(u: str) -> bool:
        color[u] = GRAY
        for v in adj.get(u, []):
            c = color.get(v, WHITE)
            if c == GRAY:
                return True
            if c == WHITE and visit(v):
                return True
        color[u] = BLACK
        return False

    nodes = {n for pair in edges for n in pair}
    return any(color.get(n, WHITE) == WHITE and visit(n) for n in nodes)


def validate_graph(
    nodes: list[dict],
    edges: list[dict],
    tail_models: dict | None = None,
    *,
    context_window_lookup: Callable[[str], int | None] | None = None,
) -> ValidationResult:
    """Validate a draft. `context_window_lookup(model_id) -> context_window|None`
    is injected (the view passes a ModelEntry-backed one); when absent the
    news_digest context-window warning is skipped (never blocks)."""
    res = ValidationResult()
    nodes = nodes or []
    edges = edges or []
    tail_models = tail_models or {}

    # ---- Node-set rules (Errors) ----------------------------------------
    type_counts: dict[str, int] = {}
    for n in nodes:
        ntype = n.get("type")
        nid = n.get("id")
        type_counts[ntype] = type_counts.get(ntype, 0) + 1

        if ntype in STRUCTURAL_TYPES:
            res.errors.append(Issue(
                "structural_in_nodes", "error",
                f"Structural node {ntype!r} must not be stored; the compiler injects it.",
                nid,
            ))
        elif ntype in TAIL_TYPES:
            res.errors.append(Issue(
                "tail_in_nodes", "error",
                f"{ntype!r} is part of the implicit, non-editable tail and must not be placed.",
                nid,
            ))
        elif ntype not in EDITABLE_TYPES:
            res.errors.append(Issue(
                "unknown_type", "error",
                f"Unknown or non-editable node type {ntype!r}.",
                nid,
            ))
        elif nid != ntype:
            # Each agent type appears at most once, so agent_name doubles as the
            # node id (the compiler relies on this).
            res.errors.append(Issue(
                "id_must_equal_type", "error",
                f"Node id {nid!r} must equal its type {ntype!r}.",
                nid,
            ))

    for ntype, count in type_counts.items():
        if count > 1:
            res.errors.append(Issue(
                "duplicate_type", "error",
                f"Node type {ntype!r} appears {count} times; each type is allowed at most once.",
                ntype,
            ))

    persona_count = sum(1 for n in nodes if n.get("type") in PERSONA_TYPES)
    analytical_count = sum(1 for n in nodes if n.get("type") in ANALYTICAL_TYPES)
    if persona_count == 0:
        res.errors.append(Issue("no_persona", "error", "At least one persona node is required."))
    if analytical_count == 0:
        res.errors.append(Issue(
            "no_analytical", "error",
            "At least one analytical node feeding analytical_join is required.",
        ))

    # ---- Edge rules (Errors) --------------------------------------------
    # Only edges among present editable nodes + the three structural joins are
    # meaningful. Compare the stored edges against the canonical wiring for this
    # node set: extras violate the entry→analytical→join / join→persona→join
    # patterns (rules 7/8); missing ones orphan a node (rule 10).
    present_ids = {n.get("id") for n in nodes if n.get("type") in EDITABLE_TYPES}
    valid_endpoints = present_ids | STRUCTURAL_TYPES
    stored = {_edge_pair(e) for e in edges}

    for f, t in stored:
        if f not in valid_endpoints or t not in valid_endpoints:
            res.errors.append(Issue(
                "edge_unknown_endpoint", "error",
                f"Edge {f!r}→{t!r} references a node that is not on the canvas.",
            ))

    canonical = {_edge_pair(e) for e in canonical_edges(
        [n for n in nodes if n.get("type") in EDITABLE_TYPES]
    )}
    for f, t in stored - canonical:
        # endpoints already flagged above are skipped to avoid double-reporting
        if f in valid_endpoints and t in valid_endpoints:
            res.errors.append(Issue(
                "edge_not_canonical", "error",
                f"Edge {f!r}→{t!r} is not allowed. Analytical nodes wire "
                f"entry→node→analytical_join; personas wire analytical_join→node→persona_join.",
            ))
    for f, t in canonical - stored:
        orphan = t if f in STRUCTURAL_TYPES else f
        res.errors.append(Issue(
            "orphan_node", "error",
            f"Node {orphan!r} is not fully wired into the graph (missing edge {f!r}→{t!r}).",
            orphan,
        ))

    if _has_cycle(stored):
        res.errors.append(Issue("cycle", "error", "The graph contains a cycle."))

    # ---- tail_models keys (Error on non-selectable / unknown) -----------
    for key in tail_models:
        if key not in TAIL_SELECTABLE:
            res.errors.append(Issue(
                "tail_model_invalid_key", "error",
                f"tail_models key {key!r} is not a model-selectable tail node "
                f"(allowed: {sorted(TAIL_SELECTABLE)}).",
            ))

    # ---- Warnings -------------------------------------------------------
    for n in nodes:
        ntype = n.get("type")
        if ntype in EDITABLE_TYPES and ALL_AGENTS[ntype].model_selectable:
            if not n.get("model_id"):
                res.warnings.append(Issue(
                    "missing_model_id", "warning",
                    f"Node {ntype!r} has no model selected; the server will fill the default.",
                    n.get("id"),
                ))

    if context_window_lookup is not None:
        nd = next((n for n in nodes if n.get("type") == "news_digest"), None)
        if nd and nd.get("model_id"):
            try:
                cw = context_window_lookup(nd["model_id"])
            except Exception:
                cw = None
            if cw is not None and cw < NEWS_DIGEST_MIN_CONTEXT:
                res.warnings.append(Issue(
                    "news_digest_small_context", "warning",
                    f"news_digest packs ~40 news items + a 10-K excerpt; the selected "
                    f"model's {cw:,}-token context window (< {NEWS_DIGEST_MIN_CONTEXT:,}) "
                    f"may truncate.",
                    nd.get("id"),
                ))

    for key, val in tail_models.items():
        if key in TAIL_SELECTABLE and val and ":" not in str(val):
            res.warnings.append(Issue(
                "tail_model_malformed", "warning",
                f"tail_models[{key!r}]={val!r} is not in 'provider:model' form.",
            ))

    return res


def fill_default_models(nodes: list[dict], tail_models: dict | None) -> tuple[list[dict], dict]:
    """Server-fill missing model_id on selectable nodes + tail models with each
    node's default (rule 11). Returns (nodes, tail_models) copies."""
    out_nodes = []
    for n in nodes:
        n = dict(n)
        ntype = n.get("type")
        spec = ALL_AGENTS.get(ntype)
        if spec and spec.model_selectable and not n.get("model_id"):
            n["model_id"] = spec.default_model_key
        out_nodes.append(n)

    out_tail = dict(tail_models or {})
    for tname in TAIL_SELECTABLE:
        if not out_tail.get(tname):
            out_tail[tname] = ALL_AGENTS[tname].default_model_key
    return out_nodes, out_tail
