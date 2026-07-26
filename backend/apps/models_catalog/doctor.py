"""P13 stored-map doctor — heals persisted per-agent model maps after catalog
drift.

Selection/execution-seam healing (``tier_menus.heal_overrides``) keeps every
LIVE flow working the moment a model dies, but the dead ids stay written in the
database — schedules, user preferences, agent-graph versions, queued runs — so
the UI keeps showing them and every future dispatch re-heals the same rot. This
module repairs the data at rest. It runs inside ``reconcile_model_catalog``
(daily, after the sync that deactivates delisted models) and on demand via
``manage.py model_map_doctor``.

Never raises: each store is healed independently and a failure in one is
logged and reported, not propagated — the reconcile task must keep working
even if one app's table is unavailable.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _heal(overrides: dict, *, preset: str | None = None) -> tuple[dict, list[dict]]:
    from .tier_menus import heal_overrides

    return heal_overrides(overrides, preset=preset)


def _heal_scheduled_runs(report: list[dict]) -> None:
    from apps.schedules.models import ScheduledRun

    for sr in ScheduledRun.objects.exclude(model_overrides={}):
        if not isinstance(sr.model_overrides, dict) or not sr.model_overrides:
            continue
        healed, moves = _heal(sr.model_overrides, preset=sr.model_preset or None)
        if moves:
            sr.model_overrides = healed
            sr.save(update_fields=["model_overrides"])
            report.append({"store": "scheduled_run", "id": sr.pk, "moves": moves})


def _heal_user_prefs(report: list[dict]) -> None:
    from .models import UserModelPreferences
    from .tier_menus import tier_default

    for prefs in UserModelPreferences.objects.all():
        changed_fields: list[str] = []
        if isinstance(prefs.per_agent_defaults, dict) and prefs.per_agent_defaults:
            healed, moves = _heal(prefs.per_agent_defaults)
            if moves:
                prefs.per_agent_defaults = healed
                changed_fields.append("per_agent_defaults")
                report.append(
                    {"store": "user_prefs.per_agent", "id": prefs.pk, "moves": moves}
                )
        if isinstance(prefs.per_tier_defaults, dict) and prefs.per_tier_defaults:
            # Per-tier defaults heal WITHIN their own tier (that is their whole
            # meaning) — a dead frugal default becomes the live frugal default.
            moves = []
            healed_tiers = dict(prefs.per_tier_defaults)
            for tier, mid in prefs.per_tier_defaults.items():
                healed_map, m = _heal({"_": mid}, preset=tier)
                new = healed_map.get("_", mid)
                if new != mid:
                    healed_tiers[tier] = new
                    moves.append({"agent": f"tier:{tier}", "from": mid, "to": new})
                elif m:
                    # heal found it dead but had nothing in-tier — fall back to
                    # the tier default if one is live.
                    td = tier_default(tier)
                    if td and td != mid:
                        healed_tiers[tier] = td
                        moves.append({"agent": f"tier:{tier}", "from": mid, "to": td})
            if moves:
                prefs.per_tier_defaults = healed_tiers
                changed_fields.append("per_tier_defaults")
                report.append(
                    {"store": "user_prefs.per_tier", "id": prefs.pk, "moves": moves}
                )
        if changed_fields:
            prefs.save(update_fields=changed_fields)


def _heal_graph_versions(report: list[dict]) -> None:
    from apps.graphs.models import AgentGraphVersion

    for version in AgentGraphVersion.objects.all():
        moves: list[dict] = []
        nodes = version.nodes or []
        node_ids = {
            str(n.get("model_id")): None
            for n in nodes
            if isinstance(n, dict) and n.get("model_id")
        }
        tails = version.tail_models or {}
        probe = {f"node:{mid}": mid for mid in node_ids}
        probe.update({f"tail:{k}": v for k, v in tails.items() if v})
        if not probe:
            continue
        healed_probe, probe_moves = _heal(probe)
        if not probe_moves:
            continue
        remap = {m["from"]: m["to"] for m in probe_moves}
        new_nodes = []
        for n in nodes:
            if isinstance(n, dict) and str(n.get("model_id")) in remap:
                old = str(n["model_id"])
                n = {**n, "model_id": remap[old]}
                moves.append({"agent": n.get("type", "?"), "from": old, "to": remap[old]})
            new_nodes.append(n)
        new_tails = {}
        for k, v in tails.items():
            if str(v) in remap:
                moves.append({"agent": k, "from": str(v), "to": remap[str(v)]})
                new_tails[k] = remap[str(v)]
            else:
                new_tails[k] = v
        if moves:
            version.nodes = new_nodes
            version.tail_models = new_tails
            version.save(update_fields=["nodes", "tail_models"])
            report.append({"store": "graph_version", "id": version.pk, "moves": moves})


def _heal_queued_runs(report: list[dict]) -> None:
    from apps.runs.models import Run

    for run in Run.objects.filter(status=Run.QUEUED).exclude(model_overrides={}):
        if not isinstance(run.model_overrides, dict) or not run.model_overrides:
            continue
        healed, moves = _heal(run.model_overrides)
        if moves:
            run.model_overrides = healed
            run.save(update_fields=["model_overrides"])
            report.append({"store": "queued_run", "id": run.pk, "moves": moves})


def heal_stored_model_maps() -> list[dict]:
    """Heal every persisted per-agent model map against the live catalog.

    Returns a report: ``[{"store", "id", "moves": [{"agent","from","to"}]}]``
    covering ScheduledRun.model_overrides, UserModelPreferences per-agent and
    per-tier defaults, AgentGraphVersion node models + tail_models, and
    still-QUEUED Run rows. Rows whose picks are all live are untouched.
    """
    report: list[dict] = []
    for store_fn in (
        _heal_scheduled_runs,
        _heal_user_prefs,
        _heal_graph_versions,
        _heal_queued_runs,
    ):
        try:
            store_fn(report)
        except Exception:
            log.exception("model-map doctor: %s failed", store_fn.__name__)
    if report:
        total = sum(len(r["moves"]) for r in report)
        log.warning(
            "model-map doctor: healed %d dead model reference(s) across %d row(s)",
            total, len(report),
        )
    return report
