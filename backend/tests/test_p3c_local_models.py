"""P3-C §12 tests: local-model gap closure (discovery, /api/models/ merge,
OllamaClient.complete, Gap F validator, hybrid preset re-wire).

All paths are mocked — no live Ollama daemon required.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

User = get_user_model()


# --- §12.5.1 — discovery -----------------------------------------------


def test_discover_ollama_models_shapes_payload_correctly() -> None:
    from apps.models_catalog import ollama_discovery
    from apps.models_catalog.ollama_discovery import discover_ollama_models

    ollama_discovery._CACHE.clear()
    fake = MagicMock()
    fake.raise_for_status.return_value = None
    fake.json.return_value = {
        "models": [
            {"name": "llama3.3:70b"},
            {"model": "qwen2.5:7b"},
        ],
    }
    with patch("apps.models_catalog.ollama_discovery.httpx.get", return_value=fake):
        out = discover_ollama_models("http://localhost:11434")
    assert {m["id"] for m in out} == {"ollama:llama3.3:70b", "ollama:qwen2.5:7b"}
    for m in out:
        assert m["provider"] == "ollama"
        assert m["tier"] == "local"
        assert m["price_in_per_mtok"] == 0
        assert m["price_out_per_mtok"] == 0


def test_discover_ollama_models_returns_empty_when_unreachable() -> None:
    from apps.models_catalog import ollama_discovery
    from apps.models_catalog.ollama_discovery import discover_ollama_models

    ollama_discovery._CACHE.clear()
    with patch(
        "apps.models_catalog.ollama_discovery.httpx.get",
        side_effect=httpx.ConnectError("connection refused"),
    ):
        out = discover_ollama_models("http://localhost:11434")
    assert out == []


def test_discover_ollama_models_caches_within_ttl() -> None:
    from apps.models_catalog import ollama_discovery
    from apps.models_catalog.ollama_discovery import discover_ollama_models

    ollama_discovery._CACHE.clear()
    fake = MagicMock()
    fake.raise_for_status.return_value = None
    fake.json.return_value = {"models": [{"name": "x"}]}
    with patch(
        "apps.models_catalog.ollama_discovery.httpx.get", return_value=fake,
    ) as mock_get:
        discover_ollama_models("http://h")
        discover_ollama_models("http://h")
    assert mock_get.call_count == 1


def test_discover_ollama_models_returns_empty_when_host_blank() -> None:
    from apps.models_catalog.ollama_discovery import discover_ollama_models

    assert discover_ollama_models("") == []
    assert discover_ollama_models(None) == []  # type: ignore[arg-type]


# --- §12.5.2 — /api/models/ Ollama merge --------------------------------


@pytest.mark.django_db
def test_models_endpoint_includes_local_when_host_set() -> None:
    from apps.models_catalog import ollama_discovery
    from apps.models_catalog.models import ProviderKey

    ollama_discovery._CACHE.clear()
    user = User.objects.create_user(email="ol@o.com", password="x" * 12)
    pk, _ = ProviderKey.objects.get_or_create(user=user)
    pk.ollama_host = "http://localhost:11434"
    pk.save()
    c = APIClient()
    tok = c.post(
        reverse("login"),
        {"email": "ol@o.com", "password": "x" * 12},
        format="json",
    ).data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}")
    fake = MagicMock()
    fake.raise_for_status.return_value = None
    fake.json.return_value = {"models": [{"name": "llama3.3:8b"}]}
    with patch("apps.models_catalog.ollama_discovery.httpx.get", return_value=fake):
        resp = c.get(reverse("models-catalog"))
    assert resp.status_code == 200
    models = resp.data["models"]
    local = [m for m in models if m["id"] == "ollama:llama3.3:8b"]
    assert len(local) == 1
    assert local[0]["available"] is True


@pytest.mark.django_db
def test_models_endpoint_omits_local_when_no_host() -> None:
    from apps.models_catalog import ollama_discovery

    ollama_discovery._CACHE.clear()
    User.objects.create_user(email="nh@o.com", password="x" * 12)
    c = APIClient()
    tok = c.post(
        reverse("login"),
        {"email": "nh@o.com", "password": "x" * 12},
        format="json",
    ).data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}")
    resp = c.get(reverse("models-catalog"))
    assert resp.status_code == 200
    assert not any(m["id"].startswith("ollama:") for m in resp.data["models"])


# --- §12.5.3 — OllamaClient.complete -----------------------------------


def test_ollama_client_complete_returns_response_with_zero_cost() -> None:
    from hedgefund_agents.llm.adapters.ollama import OllamaClient
    from hedgefund_agents.llm.client import Message

    http = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [
            {"message": {"content": "hi"}, "finish_reason": "stop"},
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }
    http.post.return_value = resp
    client = OllamaClient(host="http://x:11434", http=http)
    out = client.complete(
        model="llama3.3:8b",
        messages=[Message(role="user", content="hi")],
    )
    assert out.provider == "ollama"
    assert out.cost_usd == 0.0
    assert out.prompt_tokens == 5
    assert out.completion_tokens == 2
    # JSON-mode flag flows through.
    client.complete(
        model="x",
        messages=[Message(role="user", content="x")],
        json_mode=True,
    )
    body = http.post.call_args_list[-1].kwargs["json"]
    assert body["response_format"] == {"type": "json_object"}


# --- §12.5.4 — Gap F: validate_model_overrides accepts ollama: --------


@pytest.mark.django_db
def test_run_create_accepts_ollama_override_present_in_discovery() -> None:
    from apps.models_catalog import ollama_discovery
    from apps.models_catalog.models import ProviderKey

    ollama_discovery._CACHE.clear()
    user = User.objects.create_user(email="rg@r.com", password="x" * 12)
    pk, _ = ProviderKey.objects.get_or_create(user=user)
    pk.ollama_host = "http://localhost:11434"
    pk.save()
    c = APIClient()
    tok = c.post(
        reverse("login"),
        {"email": "rg@r.com", "password": "x" * 12},
        format="json",
    ).data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}")
    fake = MagicMock()
    fake.raise_for_status.return_value = None
    fake.json.return_value = {"models": [{"name": "llama3.3:8b"}]}
    with patch(
        "apps.models_catalog.ollama_discovery.httpx.get",
        return_value=fake,
    ), patch("apps.runs.views.execute_run.delay") as mock_task:
        resp = c.post(
            reverse("run-list-create"),
            {
                "tickers": ["AAPL"],
                "as_of_date": "2024-12-31",
                "model_overrides": {"buffett": "ollama:llama3.3:8b"},
            },
            format="json",
        )
    assert resp.status_code == 201, resp.data
    mock_task.assert_called_once()


@pytest.mark.django_db
def test_run_create_rejects_ollama_override_not_in_discovery() -> None:
    from apps.models_catalog import ollama_discovery
    from apps.models_catalog.models import ProviderKey

    ollama_discovery._CACHE.clear()
    user = User.objects.create_user(email="rj@r.com", password="x" * 12)
    pk, _ = ProviderKey.objects.get_or_create(user=user)
    pk.ollama_host = "http://localhost:11434"
    pk.save()
    c = APIClient()
    tok = c.post(
        reverse("login"),
        {"email": "rj@r.com", "password": "x" * 12},
        format="json",
    ).data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}")
    fake = MagicMock()
    fake.raise_for_status.return_value = None
    fake.json.return_value = {"models": [{"name": "llama3.3:8b"}]}
    with patch(
        "apps.models_catalog.ollama_discovery.httpx.get",
        return_value=fake,
    ), patch("apps.runs.views.execute_run.delay") as mock_task:
        resp = c.post(
            reverse("run-list-create"),
            {
                "tickers": ["AAPL"],
                "as_of_date": "2024-12-31",
                "model_overrides": {"buffett": "ollama:not-there:13b"},
            },
            format="json",
        )
    assert resp.status_code == 400, resp.data
    mock_task.assert_not_called()


# --- §12.5.5 — hybrid preset resolution --------------------------------


def test_hybrid_with_local_resolves_analytical_to_ollama() -> None:
    from apps.models_catalog.presets import expand_preset

    mapping = expand_preset("hybrid", local_tier_a="ollama:llama3.3:8b")
    for agent in ("fundamentals", "technicals", "valuation", "sentiment",
                  "macro", "news_digest"):
        assert mapping[agent] == "ollama:llama3.3:8b"


def test_hybrid_without_local_resolves_to_fallback_not_literal_token() -> None:
    from apps.models_catalog.presets import HYBRID_LOCAL_FALLBACK, expand_preset

    mapping = expand_preset("hybrid")
    for agent in ("fundamentals", "macro", "news_digest"):
        assert mapping[agent] == HYBRID_LOCAL_FALLBACK
        assert mapping[agent] != "<local-tier-a>"


@pytest.mark.django_db
def test_resolve_model_overrides_passes_discovered_local_to_hybrid(settings) -> None:
    from apps.models_catalog import ollama_discovery
    from apps.models_catalog.models import ProviderKey
    from apps.portfolios.tasks import _resolve_model_overrides

    settings.LLM_DEFAULT_PRESET = "hybrid"
    ollama_discovery._CACHE.clear()
    user = User.objects.create_user(email="rs@r.com", password="x" * 12)
    pk, _ = ProviderKey.objects.get_or_create(user=user)
    pk.ollama_host = "http://localhost:11434"
    pk.save()
    # Minimal strategy stub.
    strategy = MagicMock()
    strategy.user = user
    strategy.model_preset = "hybrid"

    fake = MagicMock()
    fake.raise_for_status.return_value = None
    fake.json.return_value = {"models": [{"name": "qwen2.5:7b"}]}
    with patch(
        "apps.models_catalog.ollama_discovery.httpx.get", return_value=fake,
    ):
        out = _resolve_model_overrides(strategy)
    # qwen is in TIER_HINTS as local-A → picked.
    assert out["macro"] == "ollama:qwen2.5:7b"
    assert out["fundamentals"] == "ollama:qwen2.5:7b"
    assert out["news_digest"] == "ollama:qwen2.5:7b"
    # Decision-path agents stay on Anthropic.
    assert out["cio"].startswith("anthropic:")
