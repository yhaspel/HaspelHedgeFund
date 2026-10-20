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
    DEV_TIER_SLUGS,
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

    return get_user_model().objects.create_user(email="p13@x.test", password="pw-fake-123456789")


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
    assert moves == [{"agent": "druckenmiller", "from": _DEAD, "to": tier_default("frugal")}]


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
    with patch("apps.models_catalog.views.expand_preset", return_value=dead_expansion):
        r = auth_client.get("/api/presets/frugal/")
    assert r.status_code == 200
    served = r.data["overrides"]
    ids = {m for m in served.values() if not m.startswith("ollama:")}
    active = set(ModelEntry.objects.filter(id__in=ids, is_active=True).values_list("id", flat=True))
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
            {"tickers": ["AAPL"], "as_of_date": "2026-07-01", "model_overrides": overrides},
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
        user=user,
        tickers=["AAPL"],
        model_overrides={"buffett": _DEAD},
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
        user=user,
        name="t",
        watchlist=wl,
        cron_expression="0 9 * * 1-5",
        model_overrides={"buffett": _DEAD},
    )
    prefs = UserModelPreferences.objects.create(
        user=user,
        per_agent_defaults={"munger": _DEAD},
        per_tier_defaults={"frugal": _DEAD},
    )
    graph = AgentGraph.objects.create(user=user, name="g")
    version = AgentGraphVersion.objects.create(
        graph=graph,
        version=1,
        nodes=[{"type": "buffett", "model_id": _DEAD}],
        tail_models={"cio": _DEAD},
    )
    queued = Run.objects.create(
        user=user,
        tickers=["AAPL"],
        status=Run.QUEUED,
        model_overrides={"wood": _DEAD},
        as_of_date="2026-07-01",
    )
    ModelEntry.objects.filter(id=_DEAD).update(is_active=False)

    report = heal_stored_model_maps()

    stores = {r["store"] for r in report}
    assert {
        "scheduled_run",
        "user_prefs.per_agent",
        "user_prefs.per_tier",
        "graph_version",
        "queued_run",
    } <= stores
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
        user=user,
        name="t",
        watchlist=wl,
        cron_expression="0 9 * * 1-5",
        model_overrides={"buffett": _DEAD},
    )
    ModelEntry.objects.filter(id=_DEAD).update(is_active=False)

    class _Sync:
        deactivated: list = []
        excluded: list = []
        swept: list = []

        def as_dict(self):
            return {}

    with (
        patch.object(catalog_tasks, "_notify_operators") as notify,
        patch("apps.models_catalog.fetching.sync_tier_models", return_value=_Sync()),
        patch("apps.models_catalog.verification.verify_models", return_value=[]),
    ):
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

    # Snapshot BEFORE the chain runs: the two 404'd rungs get runtime-quarantined
    # (deactivated) by the chain itself, so the live menu shrinks as it goes.
    frugal_bare = {m.split(":", 1)[-1] for m in tier_menu("frugal")}
    client = OpenRouterClient(api_key="test-key")
    with patch.object(OpenRouterClient, "_post_with_retry", fake_post):
        resp = client.complete(
            model="nvidia/nemotron-3-nano-30b-a3b",
            messages=[],
        )
    assert len(calls) == 3
    assert len(set(calls)) == 3  # three DISTINCT models tried
    assert set(calls) <= frugal_bare  # every hop stayed in the frugal tier
    assert resp.text  # the run got a real answer
    # Runtime quarantine: the dead rungs are now inactive in the catalog with an
    # audited note, the live one untouched — so the NEXT dispatch's selection
    # healing never picks them again.
    from apps.models_catalog.quarantine import QUARANTINE_PREFIX

    for dead in calls[:2]:
        row = ModelEntry.objects.get(id=f"openrouter:{dead}")
        assert row.is_active is False
        assert row.last_verified_note.startswith(QUARANTINE_PREFIX)
        assert "HTTP 404" in row.last_verified_note
    assert ModelEntry.objects.get(id=f"openrouter:{calls[2]}").is_active is True
    live_after = {m.split(":", 1)[-1] for m in tier_menu("frugal")}
    assert not (set(calls[:2]) & live_after)
    assert calls[2] in live_after


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
        user=user,
        name="p13",
        kind="strategy",
        cash_balance=Decimal("100000"),
    )
    strategy = PortfolioStrategy.objects.create(
        user=user,
        name="s",
        universe=universe,
        portfolio=portfolio,
    )
    target = PortfolioTarget.objects.create(strategy=strategy, as_of_date="2026-07-01")
    done = Run.objects.create(
        user=user,
        tickers=["AAPL"],
        status=Run.DONE,
        as_of_date="2026-07-01",
        source=Run.STRATEGY,
    )
    Decision.objects.create(run=done, ticker="AAPL", action="buy", side="long", confidence=73)
    pending = Run.objects.create(
        user=user,
        tickers=["MSFT"],
        status=Run.QUEUED,
        as_of_date="2026-07-01",
        source=Run.STRATEGY,
    )
    PortfolioTargetRun.objects.create(
        target=target,
        run=done,
        candidate_key="AAPL",
        primary_ticker="AAPL",
        side="long",
        screener_rank=1,
    )
    PortfolioTargetRun.objects.create(
        target=target,
        run=pending,
        candidate_key="MSFT",
        primary_ticker="MSFT",
        side="long",
        screener_rank=2,
    )
    data = PortfolioTargetDetailSerializer(target).data
    rows = {r["candidate_key"]: r for r in data["candidate_runs"]}
    assert rows["AAPL"]["decision"] == {"action": "buy", "side": "long", "confidence": 73}
    assert rows["MSFT"]["decision"] is None


# --- run 1178 (2026-10-20): dead-while-listed route + runtime quarantine -------
#
# nvidia/nemotron-3-super-120b-a12b:free kept appearing in OpenRouter's
# /api/v1/models while every call 404'd ("Function '…': Not found for account"),
# so the presence-only catalog sync never retired it; its dev-tier siblings had
# been delisted, so the :free chain had no rung and the run ended FAILED.


def _or_entry(slug: str, p_in: str = "0", p_out: str = "0") -> dict:
    return {
        "id": slug,
        "name": slug,
        "pricing": {"prompt": p_in, "completion": p_out},
        "top_provider": {"context_length": 131072},
        "supported_parameters": ["structured_outputs"],
    }


_FETCH = "apps.models_catalog.verification.fetch_openrouter_catalog"


def _or_catalog(exclude: tuple[str, ...] = ()) -> dict:
    """Fake OpenRouter catalog listing EVERY openrouter row (active or not) except
    `exclude` — dev slugs free, everything else cheap enough for frugal."""
    from apps.models_catalog.tier_menus import DEV_TIER_SLUGS

    dev = set(DEV_TIER_SLUGS)
    cat: dict = {}
    for row in ModelEntry.objects.filter(provider="openrouter"):
        if row.id in exclude:
            continue
        slug = row.id.removeprefix("openrouter:")
        cat[slug] = _or_entry(slug) if slug in dev else _or_entry(slug, "0.0000001", "0.0000001")
    return cat


@pytest.mark.django_db
def test_quarantine_deactivates_only_on_dead_route_statuses() -> None:
    from apps.models_catalog.quarantine import QUARANTINE_PREFIX, quarantine_model

    mid = "openrouter:nvidia/nemotron-3.5-super-vl:free"
    assert ModelEntry.objects.get(id=mid).is_active
    # 401/403 are KEY problems, not model problems — never quarantine on them.
    assert quarantine_model("nvidia/nemotron-3.5-super-vl:free", status_code=401) is False
    assert quarantine_model("nvidia/nemotron-3.5-super-vl:free", status_code=403) is False
    # 402 is the account's/key's credits; a data-policy or provider-allow-list
    # 404 is the account's settings — none of them says the MODEL is dead.
    for status, body in (
        (402, "Insufficient credits"),
        (404, "No endpoints found matching your data policy (Free model publication)."),
        (404, "No allowed providers are available for the selected model."),
    ):
        assert (
            quarantine_model("nvidia/nemotron-3.5-super-vl:free", status_code=status, body=body)
            is False
        )
    assert ModelEntry.objects.get(id=mid).is_active
    # 404 on a live call: deactivated with an audited note (bare slug resolves to
    # its openrouter: row); a second report is a no-op.
    assert (
        quarantine_model(
            "nvidia/nemotron-3.5-super-vl:free", status_code=404, body="Function not found"
        )
        is True
    )
    row = ModelEntry.objects.get(id=mid)
    assert row.is_active is False
    assert row.last_verified_note.startswith(QUARANTINE_PREFIX)
    assert "HTTP 404" in row.last_verified_note and "Function not found" in row.last_verified_note
    assert row.last_verified_at is not None
    assert quarantine_model(mid, status_code=404) is False
    # Unknown model: nothing to do, nothing raised.
    assert quarantine_model("nobody/nothing:free", status_code=404) is False


@pytest.mark.django_db
def test_sync_does_not_resurrect_quarantined_row_until_ttl(settings) -> None:
    """The presence-only sync used to flip a dead-while-listed slug straight back
    to active. Inside LLM_RUNTIME_QUARANTINE_DAYS it stays inactive (metadata
    still refreshed); once the TTL lapses the sync may reactivate it — if it is
    still dead the next live call re-hops and re-quarantines."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.models_catalog.fetching import sync_tier_models
    from apps.models_catalog.quarantine import quarantine_model

    settings.LLM_RUNTIME_QUARANTINE_DAYS = 3
    mid = "openrouter:nvidia/nemotron-3.5-super-vl:free"
    assert quarantine_model(mid, status_code=404, body="dead")
    with patch(_FETCH, return_value=_or_catalog()):
        res = sync_tier_models()
    row = ModelEntry.objects.get(id=mid)
    assert mid in res.quarantined and mid not in res.synced
    assert row.is_active is False
    assert row.last_verified_note.startswith("runtime quarantine:")
    assert row.context_window == 131072  # metadata still refreshed from upstream
    # TTL lapsed → the sync is allowed to bring it back.
    ModelEntry.objects.filter(id=mid).update(last_verified_at=timezone.now() - timedelta(days=4))
    with patch(_FETCH, return_value=_or_catalog()):
        res = sync_tier_models()
    row = ModelEntry.objects.get(id=mid)
    assert mid in res.synced and mid not in res.quarantined
    assert row.is_active is True
    # An operator flipping it back on ends the quarantine immediately, too.
    assert quarantine_model(mid, status_code=404, body="dead again")
    ModelEntry.objects.filter(id=mid).update(is_active=True)
    with patch(_FETCH, return_value=_or_catalog()):
        res = sync_tier_models()
    assert mid in res.synced


@pytest.mark.django_db
def test_live_synced_tier_gains_new_allowlist_members_without_cold_start() -> None:
    """A slug added to DEV_TIER_SLUGS after a deployment's cold-start seed used to
    never reach that deployment's DB menu. Both post_migrate (seed_tiers) and the
    daily sync now append missing allowlist ids — without touching an
    operator-emptied tier — in curated allowlist order (see the prod-shaped
    re-sequencing test below)."""
    from apps.models_catalog.fetching import sync_tier_models
    from apps.models_catalog.models import TierConfig, TierMembership
    from apps.models_catalog.seed import seed_tiers
    from apps.models_catalog.tier_menus import DEV_TIER_SLUGS

    dev = TierConfig.objects.get(tier_name="dev")
    dropped = f"openrouter:{DEV_TIER_SLUGS[-1]}"
    TierMembership.objects.filter(tier=dev, model_id=dropped).delete()
    kept = list(
        TierMembership.objects.filter(tier=dev)
        .order_by("ordering")
        .values_list("model_id", flat=True)
    )
    assert dropped not in kept and kept  # simulate a pre-re-curation DB

    seed_tiers()  # next post_migrate
    after = list(
        TierMembership.objects.filter(tier=dev)
        .order_by("ordering")
        .values_list("model_id", flat=True)
    )
    assert after[: len(kept)] == kept  # the curated prefix keeps its order
    assert after[-1] == dropped  # the allowlist's last slug lands last
    assert sorted(after) == sorted(f"openrouter:{s}" for s in DEV_TIER_SLUGS)
    assert dropped in tier_menu("dev")

    # The sync path does the same (and reports it); idempotent on a full menu.
    TierMembership.objects.filter(tier=dev, model_id=dropped).delete()
    with patch(_FETCH, return_value=_or_catalog()):
        res = sync_tier_models()
    assert res.membership_added == [dropped]
    with patch(_FETCH, return_value=_or_catalog()):
        res = sync_tier_models()
    assert res.membership_added == []

    # Frugal is live-synced too; a static (Anthropic-anchored) tier is not touched.
    research_before = TierMembership.objects.filter(tier_id="research").count()
    seed_tiers()
    assert TierMembership.objects.filter(tier_id="research").count() == research_before


@pytest.mark.django_db
def test_seed_reanchors_dead_live_synced_default_only() -> None:
    from apps.models_catalog.models import TierConfig
    from apps.models_catalog.seed import seed_tiers

    dev = TierConfig.objects.get(tier_name="dev")
    # Operator picks a LIVE non-default model → the seed must leave it alone.
    live_pick = ModelEntry.objects.get(id="openrouter:minimax/minimax-m3.1-flash:free")
    dev.default_model = live_pick
    dev.save(update_fields=["default_model"])
    seed_tiers()
    assert TierConfig.objects.get(tier_name="dev").default_model_id == live_pick.id
    # The stored default DIES (inactive) → re-anchor on the curated default.
    ModelEntry.objects.filter(id=live_pick.id).update(is_active=False)
    seed_tiers()
    assert (
        TierConfig.objects.get(tier_name="dev").default_model_id
        == "openrouter:nvidia/nemotron-3.5-super-vl:free"
    )


@pytest.mark.django_db
def test_run_1178_dead_free_pick_with_no_live_siblings_still_heals(settings) -> None:
    """The exact production shape: the configured :free model 404s AND its tier's
    other members are all inactive (delisted). Pre-fix the chain was empty (the
    PAID last resort is out of a :free route's price class) and ModelUnavailable
    killed the run. Now the static :free ladder + free last resort carry the
    call, the dead row is quarantined, and the stored preset heals off it."""
    from apps.backtests.exceptions import ModelUnavailable  # noqa: F401 — documents the old failure
    from apps.models_catalog.quarantine import QUARANTINE_PREFIX
    from hedgefund_agents.llm.adapters.openrouter import OpenRouterClient

    settings.OPENROUTER_API_KEY = "test-key"
    settings.LLM_SELF_HEAL = True
    settings.LLM_MAX_MODEL_FALLBACKS = 5
    settings.OPENROUTER_PAID_FALLBACK = False
    dead_id = "openrouter:nvidia/nemotron-3-super-120b-a12b:free"
    # Resurrect the dead row as the tier's ONLY active member (the pre-fix DB).
    ModelEntry.objects.filter(id=dead_id).update(is_active=True)
    ModelEntry.objects.filter(id__in=[f"openrouter:{s}" for s in DEV_TIER_SLUGS]).update(
        is_active=False
    )
    from apps.models_catalog.models import TierConfig, TierMembership

    dev = TierConfig.objects.get(tier_name="dev")
    TierMembership.objects.get_or_create(tier=dev, model_id=dead_id, defaults={"ordering": 99})
    calls: list[str] = []

    def nvidia_function_gone(self, body, headers):
        calls.append(body["model"])
        if body["model"] == "nvidia/nemotron-3-super-120b-a12b:free":
            return _mk_resp(
                404,
                text='{"error":{"message":"Provider returned error","code":404,'
                '"metadata":{"raw":"Function \'ac74…\': Not found for account"}}}',
            )
        return _mk_resp(200, _ok_payload(body["model"]))

    with patch.object(OpenRouterClient, "_post_with_retry", nvidia_function_gone):
        resp = OpenRouterClient(api_key="test-key").complete(
            model="nvidia/nemotron-3-super-120b-a12b:free", messages=[]
        )
    assert resp.text == '{"ok": true}'
    assert calls == ["nvidia/nemotron-3-super-120b-a12b:free", DEV_TIER_SLUGS[0]]
    assert resp.model == DEV_TIER_SLUGS[0] and resp.model.endswith(":free")
    assert resp.cost_usd == 0.0
    dead = ModelEntry.objects.get(id=dead_id)
    assert dead.is_active is False and dead.last_verified_note.startswith(QUARANTINE_PREFIX)
    # Selection healing now sees it dead: the next dispatch never picks it.
    healed, moves = heal_overrides({"cio": dead_id}, preset="dev")
    assert moves and healed["cio"] != dead_id


def test_free_suffix_is_priced_zero_without_a_catalog_row() -> None:
    """A :free rung that completes after its catalog row was quarantined (inactive
    rows are not priced) must not blow up the healed call as 'unpriced'."""
    from hedgefund_agents.llm.pricing import estimate_cost

    assert estimate_cost("somebody/brand-new-route:free", 1000, 1000, strict=True) == 0.0


@pytest.mark.django_db
def test_account_level_failure_never_quarantines(settings) -> None:
    """Out of credits (402) or a data-policy 404 is the ACCOUNT, not the model:
    it fails every rung of the chain. The run fails as before, but nothing is
    deactivated — the eager quarantine took a whole tier offline for days on one
    agent call. Only a 404 that a LATER rung contrasts (it answered) quarantines,
    and never a 402 or an account-scoped 404 body even then."""
    from apps.backtests.exceptions import ModelUnavailable
    from hedgefund_agents.llm.adapters.openrouter import OpenRouterClient

    settings.OPENROUTER_API_KEY = "test-key"
    settings.LLM_SELF_HEAL = True
    settings.LLM_MAX_MODEL_FALLBACKS = 5
    settings.OPENROUTER_PAID_FALLBACK = False
    active = set(ModelEntry.objects.filter(is_active=True).values_list("id", flat=True))
    first = f"openrouter:{DEV_TIER_SLUGS[0]}"

    for status, text in (
        (402, "Insufficient credits"),
        (404, "No endpoints found matching your data policy"),
        (404, "Function not found"),  # every rung dead: no contrast, no quarantine
    ):
        calls: list[str] = []

        def account_down(self, body, headers, _status=status, _text=text, _calls=calls):
            _calls.append(body["model"])
            return _mk_resp(_status, text=_text)

        with patch.object(OpenRouterClient, "_post_with_retry", account_down):
            with pytest.raises(ModelUnavailable):
                OpenRouterClient(api_key="test-key").complete(model=DEV_TIER_SLUGS[0], messages=[])
        assert len(set(calls)) == 6  # the chain walked every rung
        assert set(ModelEntry.objects.filter(is_active=True).values_list("id", flat=True)) == active

    for status, text in (
        (402, "This request requires more credits"),
        (404, "No endpoints found matching your data policy"),
    ):
        calls = []

        def then_ok(self, body, headers, _status=status, _text=text, _calls=calls):
            _calls.append(body["model"])
            if len(_calls) == 1:
                return _mk_resp(_status, text=_text)
            return _mk_resp(200, _ok_payload(body["model"]))

        with patch.object(OpenRouterClient, "_post_with_retry", then_ok):
            resp = OpenRouterClient(api_key="test-key").complete(
                model=DEV_TIER_SLUGS[0], messages=[]
            )
        assert resp.text and len(calls) == 2
        assert ModelEntry.objects.get(id=first).is_active is True


@pytest.mark.django_db
def test_seed_places_new_rungs_by_curated_rank_on_prod_shaped_dev_tier() -> None:
    """Production's dev tier predates the re-curation: [nemotron-3-super,
    deepseek-v4-flash:free (delisted), gemma-4 (429'd all day), glm-4.5-air:free
    (delisted)]. Appending at the end left gemma-4 FIRST in the live menu — the
    first fallback rung, heal target and default fallthrough. The deploy's
    post_migrate now places each new rung by its curated rank (ahead of gemma-4)
    without reordering existing rows, so an operator's own order survives too."""
    from apps.models_catalog.models import TierConfig, TierMembership
    from apps.models_catalog.seed import seed_tiers

    dev = TierConfig.objects.get(tier_name="dev")
    n3s = "openrouter:nvidia/nemotron-3-super-120b-a12b:free"
    deepseek = "openrouter:deepseek/deepseek-v4-flash:free"
    gemma = "openrouter:google/gemma-4-31b-it:free"
    glm = "openrouter:z-ai/glm-4.5-air:free"
    ModelEntry.objects.filter(id__in=[n3s, deepseek, glm]).update(is_active=False)
    TierMembership.objects.filter(tier=dev).delete()
    for ordering, mid in enumerate([n3s, deepseek, gemma, glm]):
        TierMembership.objects.create(tier=dev, model_id=mid, ordering=ordering)

    def order() -> list[str]:
        return list(
            TierMembership.objects.filter(tier=dev)
            .order_by("ordering")
            .values_list("model_id", flat=True)
        )

    seed_tiers()  # the deploy's post_migrate

    curated = [f"openrouter:{s}" for s in DEV_TIER_SLUGS]
    assert order() == [n3s, deepseek, *curated[:-1], gemma, glm]
    assert tier_menu("dev") == curated  # gemma-4 last, not first
    assert tier_menu("dev")[0] != gemma and tier_menu("dev")[-1] == gemma

    # Nothing missing → no reorder: an operator's own order survives post_migrate.
    TierMembership.objects.filter(tier=dev, model_id=curated[0]).update(ordering=50)
    seed_tiers()
    assert TierMembership.objects.get(tier=dev, model_id=curated[0]).ordering == 50

    # The tier editor only accepts ACTIVE models, so an operator reorder saved
    # while an allowlist member is dead leaves it out; the next reconcile re-adds
    # it at its rank without undoing the operator's order of the others.
    xing = curated[4]
    ModelEntry.objects.filter(id=xing).update(is_active=False)
    operator = [curated[1], curated[0], curated[3], curated[2], gemma]
    TierMembership.objects.filter(tier=dev).delete()
    for ordering, mid in enumerate(operator):
        TierMembership.objects.create(tier=dev, model_id=mid, ordering=ordering)
    seed_tiers()
    assert order() == [*operator[:-1], xing, gemma]


@pytest.mark.django_db
def test_sync_releases_lapsed_quarantine_outside_the_allowlist(settings) -> None:
    """The allowlist pass re-activates only dev/frugal rows once the TTL lapses,
    so a quarantined research/quality route never came back (no admin toggle).
    Any lapsed quarantine still listed upstream is now released; a row the seed
    retires on purpose stays retired."""
    from datetime import timedelta
    from decimal import Decimal

    from django.utils import timezone

    from apps.models_catalog.fetching import sync_tier_models
    from apps.models_catalog.quarantine import QUARANTINE_PREFIX, quarantine_model

    settings.LLM_RUNTIME_QUARANTINE_DAYS = 3
    outside = "openrouter:acme/research-reasoner"
    ModelEntry.objects.create(
        id=outside,
        provider="openrouter",
        display_name="Acme Research Reasoner",
        tier="hosted_open",
        price_in_per_mtok=Decimal("1"),
        price_out_per_mtok=Decimal("2"),
    )
    retired = "openrouter:nvidia/nemotron-3-super-120b-a12b:free"
    assert quarantine_model(outside, status_code=404, body="dead")
    ModelEntry.objects.filter(id=retired).update(
        is_active=False,
        last_verified_note=f"{QUARANTINE_PREFIX} HTTP 404 on a live call",
        last_verified_at=timezone.now() - timedelta(days=10),
    )

    with patch(_FETCH, return_value=_or_catalog()):
        res = sync_tier_models()
    assert ModelEntry.objects.get(id=outside).is_active is False  # inside the TTL
    assert outside not in res.synced

    ModelEntry.objects.filter(id=outside).update(
        last_verified_at=timezone.now() - timedelta(days=4)
    )
    with patch(_FETCH, return_value=_or_catalog()):
        res = sync_tier_models()
    row = ModelEntry.objects.get(id=outside)
    assert row.is_active is True and row.last_verified_note == ""
    assert outside in res.synced
    assert ModelEntry.objects.get(id=retired).is_active is False
