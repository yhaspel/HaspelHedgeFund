"""BLOCK_ANTHROPIC enforcement.

Two-layer guarantee for environments that must never call the Anthropic API
(dev, sometimes CI):

  A. `AnthropicClient.__init__` raises before any HTTP — even if an agent
     somehow resolves to "anthropic:...". Catches direct shell calls,
     ad-hoc scripts, and future call-site bugs we haven't enumerated.

  B. `DEFAULT_MODELS` swaps every persona/decision slot off Haiku and onto
     OpenRouter. Without this, an incomplete per-agent override map (the
     run-74 scenario) falls through to the production Haiku default and
     triggers (A) — crashing a happy-path run instead of routing it.
"""
from __future__ import annotations

import pytest
from django.test import override_settings

# ----- A: adapter-level block -------------------------------------------


@override_settings(BLOCK_ANTHROPIC=True)
def test_anthropic_client_init_raises_when_blocked() -> None:
    from hedgefund_agents.llm.adapters import AnthropicClient

    with pytest.raises(RuntimeError, match="BLOCK_ANTHROPIC"):
        AnthropicClient(api_key="ignored-by-the-block")


@override_settings(BLOCK_ANTHROPIC=False)
def test_anthropic_client_init_succeeds_when_unblocked() -> None:
    from hedgefund_agents.llm.adapters import AnthropicClient

    client = AnthropicClient(api_key="sk-ant-test-key")
    assert client.api_key == "sk-ant-test-key"


# ----- B: DEFAULT_MODELS fallback never points at anthropic -------------


@override_settings(BLOCK_ANTHROPIC=True)
def test_default_models_contains_zero_anthropic_entries_when_blocked() -> None:
    from hedgefund_agents.registry import _compute_default_models

    resolved = _compute_default_models()
    providers = {provider for (provider, _model) in resolved.values()}
    assert providers == {"openrouter"}, resolved
    # Every agent in the canonical list must be present — otherwise an
    # agent's call site does `DEFAULT_MODELS[name]` and KeyErrors.
    expected_agents = {
        "fundamentals", "technicals", "valuation", "sentiment",
        "macro", "news_digest",
        "buffett", "munger", "graham", "wood",
        "druckenmiller", "burry", "damodaran", "lynch",
        "risk_manager", "cio",
    }
    assert set(resolved.keys()) == expected_agents


@override_settings(BLOCK_ANTHROPIC=False)
def test_default_models_uses_anthropic_personas_when_unblocked() -> None:
    from hedgefund_agents.registry import _compute_default_models

    resolved = _compute_default_models()
    # Production keeps Haiku on personas + decision-path agents.
    assert resolved["buffett"][0] == "anthropic"
    assert resolved["risk_manager"][0] == "anthropic"
    # Analytical agents stay on the OpenRouter Llama route.
    assert resolved["fundamentals"][0] == "openrouter"


# ----- Integration: incomplete overrides + block -------------------------


@override_settings(BLOCK_ANTHROPIC=True)
def test_incomplete_overrides_with_block_resolves_to_openrouter_only() -> None:
    """The run-74 regression: per-agent overrides only cover 14 of 17 agents.
    The 3 personas without an override (burry/damodaran/lynch) must resolve
    to an OpenRouter model — not the prod Anthropic Haiku default.
    """
    from hedgefund_agents.base import pick_model
    from hedgefund_agents.registry import _compute_default_models

    defaults = _compute_default_models()
    overrides = {
        # Simulate: user clicked "Apply to all" but the agents list was
        # missing burry/damodaran/lynch at click time.
        "buffett": "openrouter:qwen/qwen3.6-27b",
        "munger": "openrouter:qwen/qwen3.6-27b",
    }
    state = {"model_overrides": overrides}
    for agent in ("burry", "damodaran", "lynch"):
        provider, _model = pick_model(state, agent, defaults[agent])
        assert provider == "openrouter", agent


# ----- LLM_FREE_ONLY filter ---------------------------------------------


@pytest.mark.django_db
@override_settings(LLM_FREE_ONLY=True, OPENROUTER_API_KEY="test-key")
def test_models_endpoint_marks_paid_openrouter_as_unavailable_in_free_only() -> None:
    """In dev (LLM_FREE_ONLY=True), every paid OpenRouter row in the catalog
    must come back available=False so the UI dropdowns disable them.
    Free :free rows stay available.
    """
    from django.contrib.auth import get_user_model
    from django.urls import reverse
    from rest_framework.test import APIClient

    User = get_user_model()
    User.objects.create_user(email="f@f.com", password="x" * 12)
    c = APIClient()
    tok = c.post(
        reverse("login"),
        {"email": "f@f.com", "password": "x" * 12},
        format="json",
    ).data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}")
    resp = c.get(reverse("models-catalog"))
    assert resp.status_code == 200
    rows_by_id = {m["id"]: m for m in resp.data["models"]}

    # Paid OpenRouter (in the existing seed): must be unavailable.
    assert rows_by_id["openrouter:meta-llama/llama-3.3-70b-instruct"]["available"] is False
    assert rows_by_id["openrouter:qwen/qwen3.6-27b"]["available"] is False
    # Free OpenRouter: stay available.
    assert rows_by_id["openrouter:openai/gpt-oss-120b:free"]["available"] is True
    assert rows_by_id["openrouter:nvidia/nemotron-3-super-120b-a12b:free"]["available"] is True


@pytest.mark.django_db
@override_settings(LLM_FREE_ONLY=False, OPENROUTER_API_KEY="test-key")
def test_models_endpoint_keeps_paid_openrouter_available_when_not_restricted() -> None:
    from django.contrib.auth import get_user_model
    from django.urls import reverse
    from rest_framework.test import APIClient

    User = get_user_model()
    User.objects.create_user(email="np@np.com", password="x" * 12)
    c = APIClient()
    tok = c.post(
        reverse("login"),
        {"email": "np@np.com", "password": "x" * 12},
        format="json",
    ).data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}")
    resp = c.get(reverse("models-catalog"))
    rows_by_id = {m["id"]: m for m in resp.data["models"]}
    assert rows_by_id["openrouter:meta-llama/llama-3.3-70b-instruct"]["available"] is True


# ----- Dev preset only resolves to free :free models --------------------


def test_dev_preset_expands_to_only_free_models() -> None:
    from apps.models_catalog.presets import expand_preset

    expanded = expand_preset("dev")
    assert expanded, "dev preset must expand to a non-empty per-agent map"
    for agent, model_id in expanded.items():
        assert model_id.endswith(":free"), (agent, model_id)
        assert model_id.startswith("openrouter:"), (agent, model_id)


def test_blocked_fallback_is_a_free_openrouter_slug() -> None:
    from hedgefund_agents.registry import _BLOCKED_FALLBACK

    provider, model = _BLOCKED_FALLBACK
    assert provider == "openrouter"
    assert model.endswith(":free"), model
