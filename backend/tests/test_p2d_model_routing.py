"""P2d improvements: user BYO keys flow into LLMClient creation,
Ollama is a real runtime adapter, pricing comes from ModelEntry, and
unknown model overrides are rejected at the API boundary.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

User = get_user_model()


# --- 1. Per-user provider key wiring -------------------------------------


@pytest.mark.django_db
def test_get_llm_uses_user_byo_key_when_set(settings) -> None:
    from apps.models_catalog.models import ProviderKey
    from hedgefund_agents.registry import _make_client, get_llm

    _make_client.cache_clear()

    # No platform key.
    settings.OPENROUTER_API_KEY = ""

    u = User.objects.create_user(email="b@b.com", password="x" * 12)
    pk = ProviderKey.objects.create(user=u)
    pk.set_key("openrouter", "sk-or-v1-user-test")
    pk.save()

    client = get_llm("openrouter", user_id=u.id)
    assert client.api_key == "sk-or-v1-user-test"


@pytest.mark.django_db
def test_get_llm_falls_back_to_platform_key(settings) -> None:
    from hedgefund_agents.registry import _make_client, get_llm

    _make_client.cache_clear()
    settings.OPENROUTER_API_KEY = "platform-key"

    u = User.objects.create_user(email="c@c.com", password="x" * 12)
    client = get_llm("openrouter", user_id=u.id)
    # No user key set → adapter falls back to settings.OPENROUTER_API_KEY.
    assert client.api_key == "platform-key"


# --- 2. Ollama adapter is wired into get_llm -----------------------------


@pytest.mark.django_db
def test_get_llm_supports_ollama_provider() -> None:
    from hedgefund_agents.llm.adapters import OllamaClient
    from hedgefund_agents.registry import _make_client, get_llm

    _make_client.cache_clear()
    client = get_llm("ollama")
    assert isinstance(client, OllamaClient)


def test_get_llm_rejects_unknown_provider() -> None:
    from hedgefund_agents.registry import _make_client, get_llm

    _make_client.cache_clear()
    with pytest.raises(ValueError):
        get_llm("not-a-provider")


# --- 3. Pricing reads from ModelEntry first ------------------------------


@pytest.mark.django_db
def test_estimate_cost_uses_modelentry_when_present() -> None:
    from apps.models_catalog.models import ModelEntry
    from hedgefund_agents.llm.pricing import estimate_cost

    ModelEntry.objects.create(
        id="anthropic:made-up-model",
        provider="anthropic",
        display_name="Made-up",
        tier="frontier",
        price_in_per_mtok=Decimal("2.0"),
        price_out_per_mtok=Decimal("10.0"),
        is_active=True,
    )
    cost = estimate_cost("anthropic:made-up-model", 1_000_000, 1_000_000)
    # 2 + 10 = 12 USD.
    assert abs(cost - 12.0) < 1e-6


# --- 4. Model override validation ----------------------------------------


@pytest.mark.django_db
def test_create_run_rejects_unknown_model_override() -> None:
    User.objects.create_user(email="m@m.com", password="x" * 12)
    c = APIClient()
    tok = c.post(
        reverse("login"),
        {"email": "m@m.com", "password": "x" * 12},
        format="json",
    ).data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}")
    with patch("apps.runs.views.execute_run.delay") as mock_task:
        resp = c.post(
            reverse("run-list-create"),
            {
                "tickers": ["AAPL"],
                "as_of_date": "2024-12-31",
                "model_overrides": {"buffett": "anthropic:no-such-model"},
            },
            format="json",
        )
    assert resp.status_code == 400, resp.data
    assert "model_overrides" in resp.data
    mock_task.assert_not_called()


# --- 5. Quality preset uses Opus ----------------------------------------


def test_quality_preset_routes_decision_path_to_opus() -> None:
    from apps.models_catalog.presets import expand_preset

    mapping = expand_preset("quality")
    # Decision-path agents should land on Opus.
    for agent in ("buffett", "munger", "portfolio_manager", "risk_manager", "cio"):
        assert mapping[agent] == "anthropic:claude-opus-4-6", (
            f"quality preset must route {agent} to opus; got {mapping[agent]}"
        )
    # Analytical path stays on Sonnet (Opus is overkill for extraction).
    assert mapping["fundamentals"] == "anthropic:claude-sonnet-4-6"
