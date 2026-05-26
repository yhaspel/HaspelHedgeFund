"""P3-C tests: flexible OpenRouter fetch + tier-menu invariants + Gap F.

Mirrors test_openrouter_pricing_verification.py — patches
`apps.models_catalog.verification.fetch_openrouter_catalog` so nothing
touches the live OpenRouter endpoint.
"""
from __future__ import annotations

import io
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse
from rest_framework.test import APIClient

from apps.models_catalog.fetching import sync_tier_models
from apps.models_catalog.models import ModelEntry
from apps.models_catalog.tier_menus import (
    DEV_TIER_SLUGS,
    FRUGAL_TIER_SLUGS,
    STATIC_TIER_MENUS,
    tier_menu,
)

User = get_user_model()


def _free(slug: str, name: str = "test", ctx: int = 131072) -> dict:
    return {
        "id": slug,
        "name": name,
        "pricing": {"prompt": "0", "completion": "0"},
        "context_length": ctx,
        "top_provider": {"context_length": ctx},
        "supported_parameters": ["response_format"],
    }


def _paid(slug: str, p_in: str, p_out: str, name: str = "test", ctx: int = 131072) -> dict:
    return {
        "id": slug,
        "name": name,
        "pricing": {"prompt": p_in, "completion": p_out},
        "context_length": ctx,
        "top_provider": {"context_length": ctx},
        "supported_parameters": ["response_format", "structured_outputs"],
    }


def _fake_catalog(rows: list[dict]) -> dict[str, dict]:
    return {r["id"]: r for r in rows}


# --- §9.1 — creates a row for an absent allowlisted slug ----------------


@pytest.mark.django_db
def test_sync_creates_missing_row_from_live_metadata() -> None:
    slug = DEV_TIER_SLUGS[0]
    mid = f"openrouter:{slug}"
    ModelEntry.objects.filter(id=mid).delete()  # ensure absent
    catalog = _fake_catalog([_free(slug, name="GPT-OSS 120B", ctx=200000)])
    with patch(
        "apps.models_catalog.verification.fetch_openrouter_catalog",
        return_value=catalog,
    ):
        result = sync_tier_models()
    row = ModelEntry.objects.get(id=mid)
    assert row.is_active is True
    assert mid in result.synced
    assert mid in result.created
    assert row.context_window == 200000
    assert row.price_in_per_mtok == Decimal("0")
    assert row.supports_structured_output is True


# --- §9.2 — updates pricing on an existing row --------------------------


@pytest.mark.django_db
def test_sync_updates_existing_row_pricing() -> None:
    slug = FRUGAL_TIER_SLUGS[0]
    mid = f"openrouter:{slug}"
    ModelEntry.objects.update_or_create(
        id=mid,
        defaults=dict(
            provider="openrouter",
            display_name="stale",
            tier="hosted_open",
            context_window=1000,
            price_in_per_mtok=Decimal("99"),
            price_out_per_mtok=Decimal("99"),
            is_active=True,
        ),
    )
    # Upstream returns a $0.01 per-token = $10/Mtok... too high for frugal.
    # Use $0.0000001/token = $0.10/Mtok (well under ceiling).
    catalog = _fake_catalog([
        _paid(slug, p_in="0.0000001", p_out="0.0000003", name="GPT-OSS"),
    ])
    with patch(
        "apps.models_catalog.verification.fetch_openrouter_catalog",
        return_value=catalog,
    ):
        result = sync_tier_models()
    row = ModelEntry.objects.get(id=mid)
    assert row.price_in_per_mtok == Decimal("0.1000")
    assert row.price_out_per_mtok == Decimal("0.3000")
    assert mid in result.synced
    assert mid not in result.created  # row pre-existed


# --- §9.3 — dev guard: non-free dev slug excluded -----------------------


@pytest.mark.django_db
def test_dev_slug_that_went_paid_is_excluded() -> None:
    slug = DEV_TIER_SLUGS[0]
    mid = f"openrouter:{slug}"
    ModelEntry.objects.update_or_create(
        id=mid,
        defaults=dict(
            provider="openrouter", display_name="x", tier="hosted_open",
            price_in_per_mtok=Decimal("0"), price_out_per_mtok=Decimal("0"),
            is_active=True,
        ),
    )
    catalog = _fake_catalog([_paid(slug, "0.0000001", "0", name="paid")])
    with patch(
        "apps.models_catalog.verification.fetch_openrouter_catalog",
        return_value=catalog,
    ):
        result = sync_tier_models()
    assert any(e["slug"] == slug for e in result.excluded)
    row = ModelEntry.objects.get(id=mid)
    assert row.is_active is False
    assert "no longer free" in row.last_verified_note


# --- §9.4 — frugal guard: above ceiling excluded ------------------------


@pytest.mark.django_db
def test_frugal_slug_above_ceiling_is_excluded() -> None:
    slug = FRUGAL_TIER_SLUGS[0]
    mid = f"openrouter:{slug}"
    ModelEntry.objects.update_or_create(
        id=mid,
        defaults=dict(
            provider="openrouter", display_name="x", tier="hosted_open",
            price_in_per_mtok=Decimal("0"), price_out_per_mtok=Decimal("0"),
            is_active=True,
        ),
    )
    # 10 USD/Mtok in, way above ceiling (1.0)
    catalog = _fake_catalog([_paid(slug, "0.00001", "0.00001", name="pricey")])
    with patch(
        "apps.models_catalog.verification.fetch_openrouter_catalog",
        return_value=catalog,
    ):
        result = sync_tier_models()
    assert any(e["slug"] == slug for e in result.excluded)
    row = ModelEntry.objects.get(id=mid)
    assert row.is_active is False


# --- §9.5 — missing upstream → deactivate -------------------------------


@pytest.mark.django_db
def test_missing_upstream_slug_is_deactivated_not_deleted() -> None:
    slug = DEV_TIER_SLUGS[0]
    mid = f"openrouter:{slug}"
    ModelEntry.objects.update_or_create(
        id=mid,
        defaults=dict(
            provider="openrouter", display_name="x", tier="hosted_open",
            price_in_per_mtok=Decimal("0"), price_out_per_mtok=Decimal("0"),
            is_active=True,
        ),
    )
    with patch(
        "apps.models_catalog.verification.fetch_openrouter_catalog",
        return_value={},
    ):
        result = sync_tier_models()
    assert mid in result.deactivated
    row = ModelEntry.objects.get(id=mid)
    assert row.is_active is False
    # The row is still present — not deleted.
    assert ModelEntry.objects.filter(id=mid).exists()


# --- §9.6 — reactivation when catalog returns it again ------------------


@pytest.mark.django_db
def test_deactivated_row_is_reactivated_on_healthy_fetch() -> None:
    slug = DEV_TIER_SLUGS[0]
    mid = f"openrouter:{slug}"
    ModelEntry.objects.update_or_create(
        id=mid,
        defaults=dict(
            provider="openrouter", display_name="x", tier="hosted_open",
            price_in_per_mtok=Decimal("0"), price_out_per_mtok=Decimal("0"),
            is_active=False,
        ),
    )
    catalog = _fake_catalog([_free(slug, name="GPT-OSS")])
    with patch(
        "apps.models_catalog.verification.fetch_openrouter_catalog",
        return_value=catalog,
    ):
        sync_tier_models()
    row = ModelEntry.objects.get(id=mid)
    assert row.is_active is True


# --- §9.7 — resilience: failed fetch leaves DB untouched ---------------


@pytest.mark.django_db
def test_failed_fetch_makes_no_db_writes() -> None:
    slug = DEV_TIER_SLUGS[0]
    mid = f"openrouter:{slug}"
    ModelEntry.objects.update_or_create(
        id=mid,
        defaults=dict(
            provider="openrouter", display_name="baseline", tier="hosted_open",
            price_in_per_mtok=Decimal("0"), price_out_per_mtok=Decimal("0"),
            is_active=True,
            last_verified_note="pre-existing note",
        ),
    )
    before = ModelEntry.objects.get(id=mid)
    snap = (before.is_active, before.display_name, before.last_verified_note)
    with patch(
        "apps.models_catalog.verification.fetch_openrouter_catalog",
        side_effect=RuntimeError("network down"),
    ):
        with pytest.raises(RuntimeError):
            sync_tier_models()
    after = ModelEntry.objects.get(id=mid)
    assert (after.is_active, after.display_name, after.last_verified_note) == snap


# --- §9.8 — dry_run writes nothing -------------------------------------


@pytest.mark.django_db
def test_dry_run_returns_result_but_writes_nothing() -> None:
    slug = DEV_TIER_SLUGS[0]
    mid = f"openrouter:{slug}"
    ModelEntry.objects.filter(id=mid).delete()
    catalog = _fake_catalog([_free(slug)])
    with patch(
        "apps.models_catalog.verification.fetch_openrouter_catalog",
        return_value=catalog,
    ):
        result = sync_tier_models(dry_run=True)
    assert mid in result.synced
    assert not ModelEntry.objects.filter(id=mid).exists()


# --- §9.9 — menu/routing invariant -------------------------------------


def test_menu_routing_invariant_for_every_preset() -> None:
    from apps.models_catalog.presets import PRESETS, expand_preset

    for preset in PRESETS:
        mapping = expand_preset(preset)  # hybrid <local-tier-a> → fallback
        menu = set(tier_menu(preset))
        for agent, mid in mapping.items():
            assert mid in menu, (
                f"preset {preset!r}: agent {agent!r} resolves to {mid!r} "
                f"which is not in tier_menu({preset!r}) = {sorted(menu)}"
            )


# --- §9.10 — tier_menu returns the right ids ---------------------------


def test_tier_menu_shape() -> None:
    assert all(s.startswith("openrouter:") for s in tier_menu("dev"))
    assert all(s.startswith("openrouter:") for s in tier_menu("frugal"))
    assert tier_menu("research") == STATIC_TIER_MENUS["research"]
    assert tier_menu("unknown") == []


# --- §9.11 — PresetView returns menu -----------------------------------


@pytest.mark.django_db
def test_preset_view_returns_menu() -> None:
    User.objects.create_user(email="pv@p.com", password="x" * 12)
    c = APIClient()
    tok = c.post(
        reverse("login"),
        {"email": "pv@p.com", "password": "x" * 12},
        format="json",
    ).data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}")
    resp = c.get(reverse("preset-detail", args=["dev"]))
    assert resp.status_code == 200, resp.data
    assert resp.data["menu"] == tier_menu("dev")
    assert "overrides" in resp.data


# --- §9.12 — fetch endpoint shapes -------------------------------------


@pytest.mark.django_db
def test_fetch_endpoint_returns_documented_shape() -> None:
    User.objects.create_user(email="fe@f.com", password="x" * 12)
    c = APIClient()
    tok = c.post(
        reverse("login"),
        {"email": "fe@f.com", "password": "x" * 12},
        format="json",
    ).data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}")
    catalog = _fake_catalog(
        [_free(s, name=s) for s in DEV_TIER_SLUGS]
        + [_paid(s, "0.0000001", "0.0000001", name=s) for s in FRUGAL_TIER_SLUGS]
    )
    with patch(
        "apps.models_catalog.verification.fetch_openrouter_catalog",
        return_value=catalog,
    ):
        resp = c.post(reverse("models-fetch"), {}, format="json")
    assert resp.status_code == 200, resp.data
    body = resp.data
    assert "synced" in body and "created" in body
    assert "deactivated" in body and "excluded" in body
    assert "fetched_at" in body and "models" in body


@pytest.mark.django_db
def test_fetch_endpoint_returns_502_when_catalog_call_fails() -> None:
    User.objects.create_user(email="fe2@f.com", password="x" * 12)
    c = APIClient()
    tok = c.post(
        reverse("login"),
        {"email": "fe2@f.com", "password": "x" * 12},
        format="json",
    ).data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}")
    with patch(
        "apps.models_catalog.verification.fetch_openrouter_catalog",
        side_effect=RuntimeError("upstream blew up"),
    ):
        resp = c.post(reverse("models-fetch"), {}, format="json")
    assert resp.status_code == 502, resp.data


@pytest.mark.django_db
def test_fetch_endpoint_requires_authentication() -> None:
    c = APIClient()
    resp = c.post(reverse("models-fetch"), {}, format="json")
    assert resp.status_code == 401


# --- §9.13 — ceiling calibration ---------------------------------------


@pytest.mark.django_db
def test_seed_prices_pass_frugal_ceiling() -> None:
    # Stub the catalog with each frugal slug at its seed.py price (per-token)
    from apps.models_catalog.seed import CANONICAL_MODELS

    seed_prices = {
        m["id"]: (m["price_in_per_mtok"], m["price_out_per_mtok"])
        for m in CANONICAL_MODELS
    }
    rows: list[dict] = [_free(s) for s in DEV_TIER_SLUGS]
    for s in FRUGAL_TIER_SLUGS:
        mid = f"openrouter:{s}"
        p_in_per_mtok, p_out_per_mtok = seed_prices[mid]
        # Convert per-Mtok back to per-token for the stubbed payload.
        p_in_tok = str(Decimal(p_in_per_mtok) / Decimal(1_000_000))
        p_out_tok = str(Decimal(p_out_per_mtok) / Decimal(1_000_000))
        rows.append(_paid(s, p_in=p_in_tok, p_out=p_out_tok, name=s))
    catalog = _fake_catalog(rows)
    with patch(
        "apps.models_catalog.verification.fetch_openrouter_catalog",
        return_value=catalog,
    ):
        result = sync_tier_models()
    assert result.excluded == [], (
        f"a legitimately-curated frugal slug tripped the ceiling: {result.excluded}"
    )


# --- §9.14 — seed no-clobber -------------------------------------------


@pytest.mark.django_db
def test_seed_models_does_not_clobber_fetched_row() -> None:
    from apps.models_catalog.seed import seed_models

    slug = DEV_TIER_SLUGS[0]
    mid = f"openrouter:{slug}"
    ModelEntry.objects.update_or_create(
        id=mid,
        defaults=dict(
            provider="openrouter",
            display_name="LIVE-DATA-DO-NOT-OVERWRITE",
            tier="hosted_open",
            context_window=999999,
            price_in_per_mtok=Decimal("0.0001"),
            price_out_per_mtok=Decimal("0.0002"),
            is_active=True,
        ),
    )
    # Non-allowlisted Anthropic row — seed.py is authoritative.
    ant_id = "anthropic:claude-sonnet-4-6"
    ModelEntry.objects.filter(id=ant_id).update(display_name="STALE_PRE_SEED")

    seed_models(sender=None)

    fetched = ModelEntry.objects.get(id=mid)
    assert fetched.display_name == "LIVE-DATA-DO-NOT-OVERWRITE"
    # Anthropic row IS refreshed from seed.
    ant = ModelEntry.objects.get(id=ant_id)
    assert ant.display_name == "Claude Sonnet 4.6"


# --- §9.15 — static-menu freshness + Anthropic isolation ---------------


def test_static_menu_openrouter_ids_are_in_dev_or_frugal_allowlist() -> None:
    dev_frugal = {f"openrouter:{s}" for s in (*DEV_TIER_SLUGS, *FRUGAL_TIER_SLUGS)}
    for preset, menu in STATIC_TIER_MENUS.items():
        for mid in menu:
            if mid.startswith("openrouter:"):
                assert mid in dev_frugal, (
                    f"static-menu OpenRouter id {mid!r} (preset {preset!r}) "
                    f"is not in DEV_TIER_SLUGS ∪ FRUGAL_TIER_SLUGS — "
                    f"sync_tier_models would never refresh its pricing"
                )


def test_no_anthropic_slug_in_openrouter_allowlist() -> None:
    for s in DEV_TIER_SLUGS:
        assert not s.startswith("anthropic/"), f"dev: {s}"
    for s in FRUGAL_TIER_SLUGS:
        assert not s.startswith("anthropic/"), f"frugal: {s}"


# --- Bonus: dev/frugal persona spread (§6.8) ---------------------------


def test_dev_preset_spreads_personas_across_distinct_models() -> None:
    from apps.models_catalog.presets import PERSONA_AGENTS, expand_preset

    mapping = expand_preset("dev")
    persona_models = {mapping[p] for p in PERSONA_AGENTS}
    assert len(persona_models) == len(PERSONA_AGENTS), (
        f"dev personas should resolve to {len(PERSONA_AGENTS)} distinct models; "
        f"got {len(persona_models)}: {persona_models}"
    )
    allow = {f"openrouter:{s}" for s in DEV_TIER_SLUGS}
    assert persona_models <= allow


def test_frugal_preset_spreads_personas_across_distinct_models() -> None:
    from apps.models_catalog.presets import PERSONA_AGENTS, expand_preset

    mapping = expand_preset("frugal")
    persona_models = {mapping[p] for p in PERSONA_AGENTS}
    assert len(persona_models) == len(PERSONA_AGENTS)
    allow = {f"openrouter:{s}" for s in FRUGAL_TIER_SLUGS}
    assert persona_models <= allow


# --- Management command -------------------------------------------------


@pytest.mark.django_db
def test_management_command_dry_run_exits_clean_with_no_issues() -> None:
    catalog = _fake_catalog(
        [_free(s) for s in DEV_TIER_SLUGS]
        + [_paid(s, "0.0000001", "0.0000001", name=s) for s in FRUGAL_TIER_SLUGS]
    )
    buf = io.StringIO()
    with patch(
        "apps.models_catalog.verification.fetch_openrouter_catalog",
        return_value=catalog,
    ):
        call_command("fetch_openrouter_models", "--dry-run", stdout=buf)
    assert "synced=" in buf.getvalue()


@pytest.mark.django_db
def test_management_command_raises_on_exclusion() -> None:
    catalog = _fake_catalog(
        [
            _paid(DEV_TIER_SLUGS[0], "0.0000001", "0", name="paid"),  # dev gone paid
        ]
        + [_free(s) for s in DEV_TIER_SLUGS[1:]]
        + [_paid(s, "0.0000001", "0.0000001", name=s) for s in FRUGAL_TIER_SLUGS]
    )
    buf = io.StringIO()
    with patch(
        "apps.models_catalog.verification.fetch_openrouter_catalog",
        return_value=catalog,
    ), pytest.raises(CommandError):
        call_command("fetch_openrouter_models", stdout=buf)
