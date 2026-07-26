"""P13 — self-healing model fallback.

Invariant under test: no flow may fail because a configured model id is dead.
Covers the selection-time healer (heal_overrides spread semantics), the healed
preset endpoint, submission healing (400 → 201), the execution seams, the
stored-map doctor, the adapter's bounded same-tier runtime chain, and the
cycle-table decision field.

The test DB is seeded by post_migrate (seed_models → seed_tiers), so the
dev/frugal/research/quality/hybrid tiers and their memberships exist.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.models_catalog.models import ModelEntry, UserModelPreferences
from apps.models_catalog.presets import PERSONA_AGENTS, expand_preset
from apps.models_catalog.tier_menus import (
    heal_overrides,
    sanitize_overrides,
    tier_default,
    tier_menu,
)

_DEAD = "openrouter:z-ai/glm-4-32b"  # seeded frugal member we kill in tests
_LIVE = "openrouter:meta-llama/llama-3.3-70b-instruct"  # frugal default


@pytest.fixture
def user(db):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        email="p13@x.test", password="pw-fake-123456789"
    )


@pytest.fixture
def auth_client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user=user)
    return c


# --- heal_overrides core ----------------------------------------------------


@pytest.mark.django_db
def test_heal_single_dead_pick_goes_to_tier_default() -> None:
    ModelEntry.objects.filter(id=_DEAD).update(is_active=False)
    healed, moves = heal_overrides({"druckenmiller": _DEAD, "buffett": _LIVE}, preset="frugal")
    assert healed["druckenmiller"] == tier_default("frugal")
    assert healed["buffett"] == _LIVE  # active picks never move
    assert moves == [
        {"agent": "druckenmiller", "from": _DEAD, "to": tier_default("frugal")}
    ]


@pytest.mark.django_db
def test_heal_spreads_multiple_dead_picks_across_menu() -> None:
    """N dead picks must NOT collapse onto one model (the all-16-agents-on-one-
    slug worker-saturation lesson) — they spread across the tier's active menu."""
    ModelEntry.objects.filter(id=_DEAD).update(is_active=False)
    dead_map = {a: _DEAD for a in sorted(PERSONA_AGENTS)}  # 8 dead picks
    healed, moves = heal_overrides(dead_map, preset="frugal")
    assert len(moves) == len(dead_map)
    targets = set(healed.values())
    assert _DEAD not in targets
    menu = set(tier_menu("frugal"))
    assert targets <= menu  # every target is an ACTIVE frugal member
    assert len(targets) > 1  # spread, not collapse


@pytest.mark.django_db
def test_heal_without_preset_resolves_tier_via_membership() -> None:
    """A stored map with no preset context (schedule/graph/strategy rows) heals
    from the dead model's own tier."""
    ModelEntry.objects.filter(id=_DEAD).update(is_active=False)
    healed, moves = heal_overrides({"cio": _DEAD})
    assert moves and healed["cio"] in set(tier_menu("frugal"))


@pytest.mark.django_db
def test_heal_unknown_id_and_bare_slug_semantics() -> None:
    # Unknown catalog id heals (to the default-preset menu / last resort)…
    healed, moves = heal_overrides({"macro": "openrouter:vendor/never-existed"})
    assert moves and healed["macro"] != "openrouter:vendor/never-existed"
    assert ModelEntry.objects.get(id=healed["macro"]).is_active
    # …while an ACTIVE bare slug is NOT misclassified as dead.
    bare = _LIVE.split(":", 1)[-1]
    healed2, moves2 = heal_overrides({"macro": bare})
    assert healed2 == {"macro": bare} and moves2 == []


@pytest.mark.django_db
def test_heal_exempts_ollama_ids() -> None:
    ov = {"buffett": "ollama:llama3.3:8b", "wood": _LIVE}
    assert heal_overrides(ov, preset="frugal") == (ov, [])


@pytest.mark.django_db
def test_heal_skips_blocked_anthropic_targets(settings) -> None:
    """Under BLOCK_ANTHROPIC a dead research-tier pick must not heal onto an
    anthropic: candidate (it would just move the failure downstream)."""
    settings.BLOCK_ANTHROPIC = True
    dead = "anthropic:claude-sonnet-4-6"
    ModelEntry.objects.filter(id=dead).update(is_active=False)
    healed, moves = heal_overrides({"cio": dead}, preset="research")
    assert moves, "the dead pick must move"
    assert not healed["cio"].startswith("anthropic:")


@pytest.mark.django_db
def test_sanitize_wrapper_keeps_map_only_contract() -> None:
    ModelEntry.objects.filter(id=_DEAD).update(is_active=False)
    out = sanitize_overrides("frugal", {"druckenmiller": _DEAD})
    assert out == {"druckenmiller": tier_default("frugal")}


# --- preset endpoint serves healed maps -------------------------------------


@pytest.mark.django_db
def test_preset_endpoint_never_serves_inactive_ids(auth_client) -> None:
    """The /runs/new prefill source: with a frugal member deactivated, the
    served overrides contain only ACTIVE models (the 2026-07-26 incident —
    the raw expansion served glm-4-32b and every submission 400'd)."""
    # Force the preset expansion to carry a dead pick regardless of hygiene:
    dead_expansion = dict(expand_preset("frugal"))
    dead_expansion["druckenmiller"] = _DEAD
    ModelEntry.objects.filter(id=_DEAD).update(is_active=False)
    with patch(
        "apps.models_catalog.views.expand_preset", return_value=dead_expansion
    ):
        r = auth_client.get("/api/presets/frugal/")
    assert r.status_code == 200
    served = r.data["overrides"]
    ids = {m for m in served.values() if not m.startswith("ollama:")}
    active = set(
        ModelEntry.objects.filter(id__in=ids, is_active=True).values_list("id", flat=True)
    )
    assert ids == active, f"served inactive ids: {ids - active}"
    assert served["druckenmiller"] != _DEAD


# --- submission heals instead of rejecting ----------------------------------


@pytest.mark.django_db
def test_run_submission_heals_dead_pick_to_201(auth_client) -> None:
    """The exact incident shape: an untouched frugal prefill carrying a since-
    deactivated slug must submit successfully (was: 400 'not a known active
    model'), and the STORED map must be the healed, live one."""
    from apps.runs.models import Run

    overrides = dict(expand_preset("frugal"))
    overrides["druckenmiller"] = _DEAD
    ModelEntry.objects.filter(id=_DEAD).update(is_active=False)
    with patch("apps.runs.views.execute_run.delay") as d:
        r = auth_client.post(
            "/api/runs/",
            {"tickers": ["AAPL"], "as_of_date": "2026-07-01",
             "model_overrides": overrides},
            format="json",
        )
    assert r.status_code == 201, r.data
    assert d.called
    run = Run.objects.get(pk=r.data["id"])
    assert run.model_overrides["druckenmiller"] != _DEAD
    assert ModelEntry.objects.get(id=run.model_overrides["druckenmiller"]).is_active


@pytest.mark.django_db
def test_run_submission_still_rejects_unknown_agent_key(auth_client) -> None:
    r = auth_client.post(
        "/api/runs/",
        {"tickers": ["AAPL"], "model_overrides": {"fundamentls": _LIVE}},
        format="json",
    )
    assert r.status_code == 400
    assert "unknown agent" in str(r.data)


# --- execution seams --------------------------------------------------------


@pytest.mark.django_db
def test_execute_run_heals_and_persists_stale_stored_map(user) -> None:
    """A queued run whose stored map went dead while waiting must execute on a
    live model AND persist the healed map (the run detail shows what ran)."""
    from apps.runs.models import Run
    from apps.runs.tasks import execute_run

    run = Run.objects.create(
        user=user, tickers=["AAPL"], model_overrides={"buffett": _DEAD},
        as_of_date="2026-07-01",
    )
    ModelEntry.objects.filter(id=_DEAD).update(is_active=False)
    seen: dict = {}

    class _Graph:
        def invoke(self, state):
            seen.update(state["model_overrides"])
            raise RuntimeError("stop after capture")  # skip the rest of execution

    with patch("apps.runs.tasks.resolve_graph", return_value=_Graph()):
        try:
            execute_run(run.id)
        except Exception:
            pass
    run.refresh_from_db()
    assert run.model_overrides["buffett"] != _DEAD
    assert seen and seen.get("buffett") == run.model_overrides["buffett"]


# --- stored-map doctor ------------------------------------------------------


@pytest.mark.django_db
def test_doctor_heals_all_stores(user) -> None:
    from apps.graphs.models import AgentGraph, AgentGraphVersion
    from apps.models_catalog.doctor import heal_stored_model_maps
    from apps.runs.models import Run
    from apps.schedules.models import ScheduledRun
    from apps.watchlists.models import Watchlist

    wl = Watchlist.objects.create(user=user, name="w")
    sr = ScheduledRun.objects.create(
        user=user, name="t", watchlist=wl, cron_expression="0 9 * * 1-5",
        model_overrides={"buffett": _DEAD},
    )
    prefs = UserModelPreferences.objects.create(
        user=user,
        per_agent_defaults={"munger": _DEAD},
        per_tier_defaults={"frugal": _DEAD},
    )
    graph = AgentGraph.objects.create(user=user, name="g")
    version = AgentGraphVersion.objects.create(
        graph=graph, version=1,
        nodes=[{"type": "buffett", "model_id": _DEAD}],
        tail_models={"cio": _DEAD},
    )
    queued = Run.objects.create(
        user=user, tickers=["AAPL"], status=Run.QUEUED,
        model_overrides={"wood": _DEAD}, as_of_date="2026-07-01",
    )
    ModelEntry.objects.filter(id=_DEAD).update(is_active=False)

    report = heal_stored_model_maps()

    stores = {r["store"] for r in report}
    assert {"scheduled_run", "user_prefs.per_agent", "user_prefs.per_tier",
            "graph_version", "queued_run"} <= stores
    sr.refresh_from_db()
    prefs.refresh_from_db()
    version.refresh_from_db()
    queued.refresh_from_db()
    assert sr.model_overrides["buffett"] != _DEAD
    assert prefs.per_agent_defaults["munger"] != _DEAD
    assert prefs.per_tier_defaults["frugal"] == tier_default("frugal")
    assert version.nodes[0]["model_id"] != _DEAD
    assert version.tail_models["cio"] != _DEAD
    assert queued.model_overrides["wood"] != _DEAD
    # Idempotent: a second pass finds nothing to do.
    assert heal_stored_model_maps() == []


@pytest.mark.django_db
def test_reconcile_task_runs_doctor(user) -> None:
    from apps.models_catalog import tasks as catalog_tasks
    from apps.schedules.models import ScheduledRun
    from apps.watchlists.models import Watchlist

    wl = Watchlist.objects.create(user=user, name="w")
    ScheduledRun.objects.create(
        user=user, name="t", watchlist=wl, cron_expression="0 9 * * 1-5",
        model_overrides={"buffett": _DEAD},
    )
    ModelEntry.objects.filter(id=_DEAD).update(is_active=False)

    class _Sync:
        deactivated: list = []
        excluded: list = []
        swept: list = []
        def as_dict(self):
            return {}

    with patch.object(catalog_tasks, "_notify_operators") as notify, \
         patch("apps.models_catalog.fetching.sync_tier_models", return_value=_Sync()), \
         patch("apps.models_catalog.verification.verify_models", return_value=[]):
        out = catalog_tasks.reconcile_model_catalog()
    assert out["healed"], "reconcile must run the doctor"
    assert notify.called  # healed-only changes still notify the operator


# --- adapter runtime chain --------------------------------------------------


def _mk_resp(status: int, payload: dict | None = None, text: str = ""):
    import json as _json

    import httpx

    content = _json.dumps(payload).encode() if payload is not None else text.encode()
    return httpx.Response(
        status_code=status,
        content=content,
        request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
    )


def _ok_payload(model: str) -> dict:
    return {
        "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        "model": model,
    }


@pytest.mark.django_db
def test_adapter_chain_walks_same_tier_candidates(settings) -> None:
    """Dead model → chain hops through same-tier catalog candidates (not just
    one last-resort hop) until one answers."""
    from hedgefund_agents.llm.adapters.openrouter import OpenRouterClient

    settings.OPENROUTER_API_KEY = "test-key"
    settings.LLM_SELF_HEAL = True
    settings.LLM_MAX_MODEL_FALLBACKS = 5
    calls: list[str] = []

    def fake_post(self, body, headers):
        calls.append(body["model"])
        if len(calls) < 3:
            return _mk_resp(404, text="dead route")
        return _mk_resp(200, _ok_payload(body["model"]))

    client = OpenRouterClient(api_key="test-key")
    with patch.object(OpenRouterClient, "_post_with_retry", fake_post):
        resp = client.complete(
            model="nvidia/nemotron-3-nano-30b-a3b",
            messages=[],
        )
    assert len(calls) == 3
    assert len(set(calls)) == 3  # three DISTINCT models tried
    frugal_bare = {m.split(":", 1)[-1] for m in tier_menu("frugal")}
    assert set(calls) <= frugal_bare  # every hop stayed in the frugal tier
    assert resp.text  # the run got a real answer


@pytest.mark.django_db
def test_adapter_chain_is_bounded_by_max_fallbacks(settings) -> None:
    from apps.backtests.exceptions import ModelUnavailable
    from hedgefund_agents.llm.adapters.openrouter import OpenRouterClient

    settings.OPENROUTER_API_KEY = "test-key"
    settings.LLM_SELF_HEAL = True
    settings.LLM_MAX_MODEL_FALLBACKS = 2
    calls: list[str] = []

    def always_dead(self, body, headers):
        calls.append(body["model"])
        return _mk_resp(404, text="dead route")

    client = OpenRouterClient(api_key="test-key")
    with patch.object(OpenRouterClient, "_post_with_retry", always_dead):
        with pytest.raises(ModelUnavailable):
            client.complete(model="nvidia/nemotron-3-nano-30b-a3b", messages=[])
    # 1 configured model + at most 2 fallbacks
    assert len(calls) == 3


@pytest.mark.django_db
def test_adapter_free_route_never_silently_goes_paid(settings) -> None:
    """A :free model's chain candidates stay :free when the paid hatch is off."""
    from hedgefund_agents.llm.adapters.openrouter import _fallback_candidates

    settings.OPENROUTER_PAID_FALLBACK = False
    free_model = "deepseek/deepseek-v4-flash:free"
    for c in _fallback_candidates(free_model, ()):
        assert c.endswith(":free"), f"paid candidate {c} offered to a :free route"


# --- cycle-table decision field ---------------------------------------------


@pytest.mark.django_db
def test_candidate_run_summary_includes_decision(user) -> None:
    from decimal import Decimal

    from apps.portfolios.models import (
        Portfolio,
        PortfolioStrategy,
        PortfolioTarget,
        PortfolioTargetRun,
        Universe,
    )
    from apps.portfolios.serializers import PortfolioTargetDetailSerializer
    from apps.runs.models import Decision, Run

    universe = Universe.objects.create(name="u-p13")
    portfolio = Portfolio.objects.create(
        user=user, name="p13", kind="strategy", cash_balance=Decimal("100000"),
    )
    strategy = PortfolioStrategy.objects.create(
        user=user, name="s", universe=universe, portfolio=portfolio,
    )
    target = PortfolioTarget.objects.create(strategy=strategy, as_of_date="2026-07-01")
    done = Run.objects.create(
        user=user, tickers=["AAPL"], status=Run.DONE, as_of_date="2026-07-01",
        source=Run.STRATEGY,
    )
    Decision.objects.create(run=done, ticker="AAPL", action="buy", side="long", confidence=73)
    pending = Run.objects.create(
        user=user, tickers=["MSFT"], status=Run.QUEUED, as_of_date="2026-07-01",
        source=Run.STRATEGY,
    )
    PortfolioTargetRun.objects.create(
        target=target, run=done, candidate_key="AAPL", primary_ticker="AAPL",
        side="long", screener_rank=1,
    )
    PortfolioTargetRun.objects.create(
        target=target, run=pending, candidate_key="MSFT", primary_ticker="MSFT",
        side="long", screener_rank=2,
    )
    data = PortfolioTargetDetailSerializer(target).data
    rows = {r["candidate_key"]: r for r in data["candidate_runs"]}
    assert rows["AAPL"]["decision"] == {"action": "buy", "side": "long", "confidence": 73}
    assert rows["MSFT"]["decision"] is None
