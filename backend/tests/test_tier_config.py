"""DB-backed tier config: read path, resilience (sanitize), reconcile.

The test DB is seeded by post_migrate (seed_models → seed_tiers), so TierConfig /
TierMembership rows for dev/frugal/research/quality/hybrid already exist and
mirror the tier_menus DEFAULT_* baseline. These tests exercise the behaviors the
DB layer adds on top: active-only menus, the per-tier-default fallback, and the
broadened reconcile sweep.
"""

from __future__ import annotations

import pytest

from apps.models_catalog.models import ModelEntry, TierConfig, TierMembership
from apps.models_catalog.presets import PERSONA_AGENTS, expand_preset
from apps.models_catalog.tier_menus import (
    sanitize_overrides,
    tier_default,
    tier_menu,
)

# A frugal-tier member used as the "delisted" model in several tests.
_FRUGAL_MEMBER = "openrouter:z-ai/glm-4-32b"
_FRUGAL_DEFAULT = "openrouter:meta-llama/llama-3.3-70b-instruct"


# --- seed sanity -----------------------------------------------------------


@pytest.mark.django_db
def test_post_migrate_seeded_all_tiers() -> None:
    assert set(TierConfig.objects.values_list("tier_name", flat=True)) == {
        "dev",
        "frugal",
        "research",
        "quality",
        "hybrid",
    }
    # frugal default + membership mirror the baseline.
    assert tier_default("frugal") == _FRUGAL_DEFAULT
    assert _FRUGAL_MEMBER in tier_menu("frugal")


@pytest.mark.django_db
def test_seeded_cheap_tiers_have_no_reasoning_member() -> None:
    """The dev/frugal menus feed decision roles, so their DB membership must stay
    reasoning-free (the qwen3.6-27b empty-content hang). DB-level guard that also
    catches a bad operator edit, not just a bad constant."""
    for tier in ("dev", "frugal"):
        bad = list(
            TierMembership.objects.filter(tier_id=tier, model__supports_reasoning=True).values_list(
                "model_id", flat=True
            )
        )
        assert not bad, f"{tier} has reasoning members: {bad}"


@pytest.mark.django_db
def test_every_tier_default_is_active_and_a_member() -> None:
    for tc in TierConfig.objects.select_related("default_model").all():
        if tc.default_model_id is None:
            continue
        assert tc.default_model.is_active, f"{tc.tier_name} default is inactive"
        assert TierMembership.objects.filter(
            tier=tc, model_id=tc.default_model_id
        ).exists(), f"{tc.tier_name} default is not a member of its own tier"


# --- active-only menu ------------------------------------------------------


@pytest.mark.django_db
def test_tier_menu_drops_deactivated_member() -> None:
    assert _FRUGAL_MEMBER in tier_menu("frugal")
    ModelEntry.objects.filter(id=_FRUGAL_MEMBER).update(is_active=False)
    assert _FRUGAL_MEMBER not in tier_menu("frugal")
    # the rest of the menu is intact
    assert _FRUGAL_DEFAULT in tier_menu("frugal")


@pytest.mark.django_db
def test_unseeded_tier_falls_back_to_constant_baseline() -> None:
    # Wipe the dev tier's DB membership → tier_menu falls back to DEFAULT_*.
    from apps.models_catalog.tier_menus import _default_menu

    TierMembership.objects.filter(tier_id="dev").delete()
    assert tier_menu("dev") == _default_menu("dev")


# --- tier_default fallback -------------------------------------------------


@pytest.mark.django_db
def test_tier_default_falls_back_to_first_active_member_when_default_dead() -> None:
    tc = TierConfig.objects.get(tier_name="frugal")
    # Point the default at a member, then deactivate that member.
    ModelEntry.objects.filter(id=_FRUGAL_MEMBER).update(is_active=True)
    tc.default_model_id = _FRUGAL_MEMBER
    tc.save(update_fields=["default_model"])
    assert tier_default("frugal") == _FRUGAL_MEMBER
    ModelEntry.objects.filter(id=_FRUGAL_MEMBER).update(is_active=False)
    # default is dead → first ACTIVE menu member instead (never the dead id)
    nxt = tier_default("frugal")
    assert nxt != _FRUGAL_MEMBER
    assert ModelEntry.objects.get(id=nxt).is_active


# --- sanitize_overrides (the resilience core) ------------------------------


@pytest.mark.django_db
def test_sanitize_replaces_only_inactive_with_tier_default() -> None:
    ModelEntry.objects.filter(id=_FRUGAL_MEMBER).update(is_active=False)
    overrides = {
        "druckenmiller": _FRUGAL_MEMBER,  # dead
        "buffett": _FRUGAL_DEFAULT,  # active
        "wood": "openrouter:google/gemma-3-27b-it",  # active
    }
    out = sanitize_overrides("frugal", overrides)
    assert out["druckenmiller"] == tier_default("frugal")  # dead → default
    assert out["buffett"] == _FRUGAL_DEFAULT  # unchanged
    assert out["wood"] == "openrouter:google/gemma-3-27b-it"  # unchanged


@pytest.mark.django_db
def test_sanitize_preserves_persona_spread() -> None:
    """Only the dead persona moves to a live model; the others keep distinct
    still-active models (risk #2 — the spread must not collapse). Since the
    P13 hygiene fix the frugal preset no longer carries glm-4-32b, so the dead
    pick is injected explicitly (as a stored/user map would carry it)."""
    ModelEntry.objects.filter(id=_FRUGAL_MEMBER).update(is_active=False)
    base = expand_preset("frugal")
    base["druckenmiller"] = _FRUGAL_MEMBER  # the dead pick
    mapping = sanitize_overrides("frugal", base)
    # the persona that used the dead slug now points at the live fallback
    assert mapping["druckenmiller"] == tier_default("frugal")
    others = {mapping[p] for p in PERSONA_AGENTS if p != "druckenmiller"}
    assert _FRUGAL_MEMBER not in others  # dead slug fully gone
    assert len(others) > 1  # spread preserved


@pytest.mark.django_db
def test_sanitize_exempts_ollama_and_active_picks() -> None:
    ov = {
        "buffett": "ollama:llama3.3:8b",  # no ModelEntry row — exempt
        "wood": _FRUGAL_DEFAULT,  # active
    }
    assert sanitize_overrides("frugal", ov) == ov


# --- Phase 3: broad reconcile sweep + scheduled task -----------------------


def _entry(slug: str, p_in: str = "0", p_out: str = "0") -> dict:
    return {
        "id": slug,
        "name": slug,
        "pricing": {"prompt": p_in, "completion": p_out},
        "top_provider": {"context_length": 131072},
        "supported_parameters": ["structured_outputs"],
    }


def _catalog_for_all_active_openrouter(exclude: tuple[str, ...] = ()) -> dict:
    """A fake OpenRouter catalog covering every active openrouter row except
    `exclude` — so a sync deactivates ONLY the excluded slug(s). Dev slugs stay
    free; everything else is cheap enough to pass the frugal ceiling guard."""
    from apps.models_catalog.tier_menus import DEV_TIER_SLUGS

    dev = set(DEV_TIER_SLUGS)
    cat: dict = {}
    for row in ModelEntry.objects.filter(is_active=True, provider="openrouter"):
        if row.id in exclude:
            continue
        slug = row.id.removeprefix("openrouter:")
        cat[slug] = _entry(slug) if slug in dev else _entry(slug, "0.0000001", "0.0000001")
    return cat


@pytest.mark.django_db
def test_sync_broad_sweep_deactivates_vanished_nonallowlist_row() -> None:
    from unittest.mock import patch

    from apps.models_catalog.fetching import sync_tier_models

    # A seeded, active, non-allowlist OpenRouter row (a "ghost" if delisted).
    ghost = "openrouter:arcee-ai/trinity-large-thinking:free"
    assert ModelEntry.objects.get(id=ghost).is_active
    cat = _catalog_for_all_active_openrouter(exclude=(ghost,))
    with patch("apps.models_catalog.verification.fetch_openrouter_catalog", return_value=cat):
        result = sync_tier_models()
    g = ModelEntry.objects.get(id=ghost)  # still EXISTS — never hard-deleted
    assert g.is_active is False
    assert result.swept == [ghost]  # ONLY the ghost swept (broad pass)
    assert result.deactivated == []  # no curated/allowlist slug died
    # Anthropic rows are not in the OpenRouter catalog and must never be touched.
    assert ModelEntry.objects.get(id="anthropic:claude-sonnet-4-6").is_active is True


@pytest.mark.django_db
def test_reconcile_task_runs_sync_and_verify() -> None:
    from unittest.mock import patch

    from apps.models_catalog.tasks import reconcile_model_catalog

    cat = _catalog_for_all_active_openrouter()
    with patch("apps.models_catalog.verification.fetch_openrouter_catalog", return_value=cat):
        out = reconcile_model_catalog()
    assert "sync" in out and "drift" in out
    assert out["sync"]["fetched_at"] is not None


@pytest.mark.django_db
def test_reconcile_notifies_email_channels_only() -> None:
    """Model-reconcile operator alerts must go to email, never Telegram."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from django.contrib.auth import get_user_model

    from apps.models_catalog.tasks import _notify_operators
    from apps.notifications.models import NotificationChannel

    staff = get_user_model().objects.create_user(
        email="ops@example.com", password="x", is_staff=True
    )
    email_ch = NotificationChannel.objects.create(
        user=staff, kind=NotificationChannel.EMAIL, is_active=True
    )
    NotificationChannel.objects.create(
        user=staff, kind=NotificationChannel.TELEGRAM, is_active=True
    )

    sync = SimpleNamespace(deactivated=["some:slug"], swept=[], excluded=[])
    with patch("apps.notifications.services.send_notification") as send:
        _notify_operators(sync, drift=[])

    sent_channels = [call.args[0] for call in send.call_args_list]
    assert sent_channels == [email_ch]


def test_beat_schedule_registers_reconcile() -> None:
    from hedgefund.celery import app

    entry = app.conf.beat_schedule.get("reconcile-model-catalog")
    assert entry is not None
    assert entry["task"] == "apps.models_catalog.tasks.reconcile_model_catalog"


# --- Phase 4: per-tier user default layering -------------------------------


def _make_user(email: str, *, staff: bool = False):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(email=email, password="supersecret", is_staff=staff)


def _client(email: str):
    from django.urls import reverse
    from rest_framework.test import APIClient

    c = APIClient()
    tok = c.post(reverse("login"), {"email": email, "password": "supersecret"}, format="json").data[
        "access"
    ]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}")
    return c


@pytest.mark.django_db
def test_user_per_tier_default_anchors_nonpersonas_keeps_spread() -> None:
    from decimal import Decimal

    from apps.models_catalog.models import UserModelPreferences
    from apps.portfolios.models import Portfolio, PortfolioStrategy, Universe
    from apps.portfolios.tasks import _resolve_model_overrides

    user = _make_user("ptd@example.com")
    anchor = "openrouter:nvidia/nemotron-3-nano-30b-a3b"  # a frugal, non-reasoning member
    UserModelPreferences.objects.create(
        user=user, preset="frugal", per_tier_defaults={"frugal": anchor}
    )
    uni = Universe.objects.create(name="ptd-uni", is_active=True)
    pf = Portfolio.objects.create(
        user=user, name="p", kind="strategy", cash_balance=Decimal("100000")
    )
    strat = PortfolioStrategy.objects.create(
        user=user, name="s", universe=uni, portfolio=pf, model_preset="frugal"
    )

    out = _resolve_model_overrides(strat)
    # non-persona roles (analytical + orchestration) anchored to the user default
    for a in ("risk_manager", "portfolio_manager", "cio", "fundamentals", "macro"):
        assert out[a] == anchor
    # personas keep the preset's spread (not collapsed to the anchor)
    assert len({out[p] for p in PERSONA_AGENTS}) > 1


@pytest.mark.django_db
def test_per_tier_defaults_validation_rejects_offmenu() -> None:
    from django.urls import reverse

    _make_user("v@example.com")
    c = _client("v@example.com")
    url = reverse("my-model-prefs")
    # opus is NOT in the frugal menu → rejected
    r = c.put(url, {"per_tier_defaults": {"frugal": "anthropic:claude-opus-4-7"}}, format="json")
    assert r.status_code == 400
    # a real frugal member is accepted + persisted
    good = "openrouter:meta-llama/llama-3.3-70b-instruct"
    r2 = c.put(url, {"per_tier_defaults": {"frugal": good}}, format="json")
    assert r2.status_code == 200
    assert r2.data["per_tier_defaults"]["frugal"] == good


# --- Phase 4: operator tier editing ----------------------------------------


@pytest.mark.django_db
def test_tier_config_requires_staff() -> None:
    from django.urls import reverse

    _make_user("plain@example.com", staff=False)
    assert _client("plain@example.com").get(reverse("tiers-list")).status_code == 403
    _make_user("boss@example.com", staff=True)
    r = _client("boss@example.com").get(reverse("tiers-list"))
    assert r.status_code == 200
    assert {t["tier_name"] for t in r.data["tiers"]} == {
        "dev",
        "frugal",
        "research",
        "quality",
        "hybrid",
    }


@pytest.mark.django_db
def test_operator_can_edit_membership_and_default() -> None:
    from django.urls import reverse

    _make_user("op@example.com", staff=True)
    c = _client("op@example.com")
    members = [
        "openrouter:meta-llama/llama-3.3-70b-instruct",
        "openrouter:z-ai/glm-4-32b",
    ]
    r = c.put(
        reverse("tier-detail", args=["frugal"]),
        {"members": members, "default_model": "openrouter:z-ai/glm-4-32b"},
        format="json",
    )
    assert r.status_code == 200
    assert [m["model_id"] for m in r.data["members"]] == members
    assert r.data["default_model"] == "openrouter:z-ai/glm-4-32b"
    # the DB-backed menu + default now reflect the edit
    assert tier_menu("frugal") == members
    assert tier_default("frugal") == "openrouter:z-ai/glm-4-32b"


@pytest.mark.django_db
def test_operator_cannot_add_reasoning_to_cheap_tier() -> None:
    from django.urls import reverse

    _make_user("op2@example.com", staff=True)
    c = _client("op2@example.com")
    # deepseek-v4-pro is a reasoning model; frugal has allow_reasoning=False
    r = c.put(
        reverse("tier-detail", args=["frugal"]),
        {"members": ["openrouter:deepseek/deepseek-v4-pro"]},
        format="json",
    )
    assert r.status_code == 400
    assert "reasoning" in r.data["detail"].lower()


# --- Review-fix regressions ------------------------------------------------


def _strategy(user, **kwargs):
    from decimal import Decimal

    from apps.portfolios.models import Portfolio, PortfolioStrategy, Universe

    uni = Universe.objects.create(name=f"u-{user.email}", is_active=True)
    pf = Portfolio.objects.create(
        user=user, name="p", kind="strategy", cash_balance=Decimal("100000")
    )
    return PortfolioStrategy.objects.create(
        user=user, name="s", universe=uni, portfolio=pf, **kwargs
    )


@pytest.mark.django_db
def test_sanitize_dead_fallback_falls_through_to_live_tier_default() -> None:
    """Bug: a dead user/operator anchor passed as `fallback` must not be used to
    'rewrite' dead picks to the same dead id — fall through to tier_default."""
    dead = _FRUGAL_MEMBER
    ModelEntry.objects.filter(id=dead).update(is_active=False)
    out = sanitize_overrides("frugal", {"cio": dead}, fallback=dead)
    assert out["cio"] != dead
    assert ModelEntry.objects.get(id=out["cio"]).is_active


def test_anchor_non_personas_skips_hybrid_and_keeps_personas() -> None:
    from apps.models_catalog.tier_menus import anchor_non_personas

    base = {"buffett": "m1", "cio": "m2", "fundamentals": "m3"}
    assert anchor_non_personas("hybrid", dict(base), "anchor") == base  # hybrid no-op
    out = anchor_non_personas("frugal", dict(base), "anchor")
    assert out["buffett"] == "m1"  # persona kept
    assert out["cio"] == "anchor"  # non-persona anchored
    assert out["fundamentals"] == "anchor"


@pytest.mark.django_db
def test_partial_per_agent_overlays_preset_and_per_tier() -> None:
    """Bug: a PARTIAL per_agent_defaults map must overlay the preset+per-tier
    base, not replace it (unset roles must NOT drop to the registry default)."""
    from apps.models_catalog.models import UserModelPreferences
    from apps.portfolios.tasks import _resolve_model_overrides

    user = _make_user("partial@example.com")
    anchor = "openrouter:nvidia/nemotron-3-nano-30b-a3b"
    UserModelPreferences.objects.create(
        user=user,
        preset="frugal",
        per_agent_defaults={"buffett": "openrouter:z-ai/glm-4-32b"},  # one agent only
        per_tier_defaults={"frugal": anchor},
    )
    out = _resolve_model_overrides(_strategy(user, model_preset="frugal"))
    assert out["buffett"] == "openrouter:z-ai/glm-4-32b"  # explicit per-agent wins
    assert out["cio"] == anchor  # non-persona anchored, not dropped
    # personas keep the preset spread (not all collapsed to the anchor)
    assert len({out[p] for p in ("munger", "graham", "wood", "druckenmiller")}) > 1


@pytest.mark.django_db
def test_members_only_put_clears_orphaned_default() -> None:
    from django.urls import reverse

    _make_user("op4@example.com", staff=True)
    c = _client("op4@example.com")
    # Replace frugal members WITHOUT the current default (llama-3.3-70b), no default_model key.
    r = c.put(
        reverse("tier-detail", args=["frugal"]),
        {"members": ["openrouter:z-ai/glm-4-32b"]},
        format="json",
    )
    assert r.status_code == 200
    assert r.data["default_model"] is None  # orphaned default cleared
    assert tier_default("frugal") == "openrouter:z-ai/glm-4-32b"


@pytest.mark.django_db
def test_invalid_default_does_not_mutate_membership() -> None:
    from django.urls import reverse

    _make_user("op6@example.com", staff=True)
    c = _client("op6@example.com")
    before = list(
        TierMembership.objects.filter(tier_id="frugal").values_list("model_id", flat=True)
    )
    # Valid members BUT a default not in the new member set → 400; membership must
    # be untouched (validate-before-write; no partial mutation).
    r = c.put(
        reverse("tier-detail", args=["frugal"]),
        {
            "members": ["openrouter:z-ai/glm-4-32b"],
            "default_model": "openrouter:meta-llama/llama-3.3-70b-instruct",
        },
        format="json",
    )
    assert r.status_code == 400
    after = list(TierMembership.objects.filter(tier_id="frugal").values_list("model_id", flat=True))
    assert after == before


@pytest.mark.django_db
def test_seed_tiers_does_not_revive_operator_emptied_tier() -> None:
    from apps.models_catalog.seed import seed_tiers

    TierMembership.objects.filter(tier_id="dev").delete()  # operator emptied dev
    seed_tiers()  # next post_migrate
    assert not TierMembership.objects.filter(tier_id="dev").exists()


@pytest.mark.django_db
def test_sync_aborts_on_empty_catalog() -> None:
    from unittest.mock import patch

    from apps.models_catalog.fetching import sync_tier_models

    before = ModelEntry.objects.filter(is_active=True, provider="openrouter").count()
    with patch("apps.models_catalog.verification.fetch_openrouter_catalog", return_value={}):
        result = sync_tier_models()
    assert result.deactivated == [] and result.swept == []
    assert ModelEntry.objects.filter(is_active=True, provider="openrouter").count() == before


@pytest.mark.django_db
def test_per_tier_defaults_rejects_hybrid() -> None:
    from django.urls import reverse

    _make_user("hy@example.com")
    c = _client("hy@example.com")
    r = c.put(
        reverse("my-model-prefs"),
        {"per_tier_defaults": {"hybrid": "anthropic:claude-sonnet-4-6"}},
        format="json",
    )
    assert r.status_code == 400
