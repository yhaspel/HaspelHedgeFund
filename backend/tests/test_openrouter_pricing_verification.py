"""Tests for the OpenRouter pricing verifier.

Covers:
  - verify_models classifies match / drift / missing correctly
  - the per-token → per-Mtok conversion
  - last_verified_at / last_verified_note are stamped on the DB row
  - the POST /api/models/verify-pricing/ endpoint returns the same shape
  - the verify_openrouter_pricing management command exit code reflects failure
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

from apps.models_catalog.models import ModelEntry
from apps.models_catalog.verification import (
    verify_models,
)

User = get_user_model()


def _fake_catalog(rows: list[dict]) -> dict[str, dict]:
    """Build a {slug: payload} dict in the shape OpenRouter /api/v1/models returns."""
    return {r["id"]: r for r in rows}


def _make_entry(*, mid: str, p_in: str = "0", p_out: str = "0") -> ModelEntry:
    return ModelEntry.objects.create(
        id=mid,
        provider="openrouter",
        display_name=mid,
        tier="hosted_open",
        context_window=131_072,
        price_in_per_mtok=Decimal(p_in),
        price_out_per_mtok=Decimal(p_out),
    )


@pytest.mark.django_db
def test_verify_models_marks_matching_pricing_as_ok() -> None:
    mid = "openrouter:test/match:free"
    _make_entry(mid=mid, p_in="0", p_out="0")
    catalog = _fake_catalog([
        {"id": "test/match:free", "pricing": {"prompt": "0", "completion": "0"}},
    ])
    with patch(
        "apps.models_catalog.verification.fetch_openrouter_catalog",
        return_value=catalog,
    ):
        results = verify_models(model_ids=[mid])
    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].note == ""
    row = ModelEntry.objects.get(id=mid)
    assert row.last_verified_at is not None
    assert row.last_verified_note == ""


@pytest.mark.django_db
def test_verify_models_detects_drift_when_free_becomes_paid() -> None:
    mid = "openrouter:test/drift:free"
    _make_entry(mid=mid, p_in="0", p_out="0")
    catalog = _fake_catalog([
        # Upstream now charges $0.10/Mtok prompt = "0.0000001" per token.
        {"id": "test/drift:free", "pricing": {"prompt": "0.0000001", "completion": "0"}},
    ])
    with patch(
        "apps.models_catalog.verification.fetch_openrouter_catalog",
        return_value=catalog,
    ):
        results = verify_models(model_ids=[mid])
    assert results[0].ok is False
    assert "drift" in results[0].note
    row = ModelEntry.objects.get(id=mid)
    assert row.last_verified_note == results[0].note


@pytest.mark.django_db
def test_verify_models_flags_missing_slug() -> None:
    mid = "openrouter:gone/model:free"
    _make_entry(mid=mid, p_in="0", p_out="0")
    with patch(
        "apps.models_catalog.verification.fetch_openrouter_catalog",
        return_value={},
    ):
        results = verify_models(model_ids=[mid])
    assert results[0].ok is False
    assert "not in OpenRouter" in results[0].note


@pytest.mark.django_db
def test_verify_models_filters_to_requested_ids() -> None:
    _make_entry(mid="openrouter:keep/me:free")
    _make_entry(mid="openrouter:skip/me:free")
    catalog = _fake_catalog([
        {"id": "keep/me:free", "pricing": {"prompt": "0", "completion": "0"}},
    ])
    with patch(
        "apps.models_catalog.verification.fetch_openrouter_catalog",
        return_value=catalog,
    ):
        results = verify_models(model_ids=["openrouter:keep/me:free"])
    assert [r.model_id for r in results] == ["openrouter:keep/me:free"]


@pytest.mark.django_db
def test_verify_endpoint_returns_results_and_refreshed_models() -> None:
    mid = "openrouter:test/api:free"
    _make_entry(mid=mid, p_in="0", p_out="0")
    User.objects.create_user(email="v@v.com", password="x" * 12)
    c = APIClient()
    tok = c.post(
        reverse("login"),
        {"email": "v@v.com", "password": "x" * 12},
        format="json",
    ).data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}")

    catalog = _fake_catalog([
        {"id": "test/api:free", "pricing": {"prompt": "0", "completion": "0"}},
    ])
    with patch(
        "apps.models_catalog.verification.fetch_openrouter_catalog",
        return_value=catalog,
    ):
        resp = c.post(
            reverse("models-verify-pricing"),
            {"model_ids": [mid]},
            format="json",
        )
    assert resp.status_code == 200, resp.content
    assert len(resp.data["results"]) == 1
    assert resp.data["results"][0]["ok"] is True
    by_id = {m["id"]: m for m in resp.data["models"]}
    assert mid in by_id
    assert by_id[mid]["last_verified_at"] is not None
    assert by_id[mid]["is_free"] is True


@pytest.mark.django_db
def test_management_command_exits_nonzero_on_drift() -> None:
    mid = "openrouter:test/cmd:free"
    _make_entry(mid=mid, p_in="0", p_out="0")
    catalog = _fake_catalog([
        {"id": "test/cmd:free", "pricing": {"prompt": "0.0000005", "completion": "0"}},
    ])
    buf = io.StringIO()
    with patch(
        "apps.models_catalog.verification.fetch_openrouter_catalog",
        return_value=catalog,
    ), pytest.raises(CommandError):
        call_command("verify_openrouter_pricing", "--model", mid, stdout=buf)
    catalog_ok = _fake_catalog([
        {"id": "test/cmd:free", "pricing": {"prompt": "0", "completion": "0"}},
    ])
    with patch(
        "apps.models_catalog.verification.fetch_openrouter_catalog",
        return_value=catalog_ok,
    ):
        call_command(
            "verify_openrouter_pricing", "--model", mid, stdout=io.StringIO()
        )


@pytest.mark.django_db
def test_model_entry_is_free_property() -> None:
    free = _make_entry(mid="openrouter:a/b:free", p_in="0", p_out="0")
    paid = _make_entry(mid="openrouter:c/d", p_in="0.5", p_out="1.0")
    assert free.is_free is True
    assert paid.is_free is False
