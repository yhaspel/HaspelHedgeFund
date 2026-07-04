"""P4-OFF — offline mode (L1) backend tests.

Covers the health probe, the provider HTTP fence, the ``local`` preset +
resolution, execution-seam preset forcing across all creation paths, the
registry LLM fence (the R3 hard guarantee), Celery no-ops, and the socket
guard. All paths are mocked — no live Ollama daemon or WAN required.
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

# Import the models-catalog view module up front so its module-level
# ``from .ollama_discovery import discover_ollama_models`` binds to the REAL
# function at collection time. Otherwise the first ``reverse()`` inside a health
# test's ``patch(...discover_ollama_models...)`` window loads the URLconf, which
# imports this view against the mock and poisons it for later test files.
import apps.models_catalog.views  # noqa: F401,E402

User = get_user_model()

FAKE_MODELS = [
    {"id": "ollama:qwen2.5:7b", "notes": "local-A"},
    {"id": "ollama:llama3.1:8b", "notes": "local-A"},
]
CLOUD = "anthropic:claude-opus-4-7"


@pytest.fixture
def user(db):
    return User.objects.create_user(email="offline@test.local", password="testpass123")


@pytest.fixture
def auth_client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _patch_discovery(models=FAKE_MODELS):
    return patch(
        "apps.models_catalog.offline.discover_ollama_models", return_value=models
    )


# ---------------------------------------------------------------------------
# Health endpoint — the client's online / L1 / L2 discriminator (WS-1.2, D4).
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_health_unauthenticated_and_online_payload():
    client = APIClient()  # no auth
    with patch(
        "apps.models_catalog.ollama_discovery.discover_ollama_models", return_value=[]
    ):
        resp = client.get(reverse("health"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["offline_mode"] is False
    assert body["llm"]["forced_preset"] is None
    assert body["llm"]["local_available"] is False
    assert "time" in body


@pytest.mark.django_db
@override_settings(OFFLINE_MODE=True, OFFLINE_LLM_MODEL="qwen2.5:7b")
def test_health_offline_payload():
    client = APIClient()
    with patch(
        "apps.models_catalog.ollama_discovery.discover_ollama_models",
        return_value=FAKE_MODELS,
    ):
        resp = client.get(reverse("health"))
    body = resp.json()
    assert body["offline_mode"] is True
    assert body["llm"]["forced_preset"] == "local"
    assert body["llm"]["local_model"] == "qwen2.5:7b"
    assert body["llm"]["local_available"] is True


# ---------------------------------------------------------------------------
# `local` preset + resolution (WS-1.4).
# ---------------------------------------------------------------------------


def test_expand_local_preset_resolves_all_agents_to_discovered_tag():
    from apps.models_catalog.presets import ALL_AGENTS, expand_preset

    m = expand_preset("local", local_model="ollama:qwen2.5:7b")
    assert set(m) == ALL_AGENTS
    assert all(v == "ollama:qwen2.5:7b" for v in m.values())
    # the literal token must never leak
    assert "<local>" not in m.values()


@override_settings(OFFLINE_LLM_MODEL="qwen2.5:7b")
def test_resolve_local_model_prefers_offline_llm_model_when_discovered():
    from apps.models_catalog.offline import resolve_local_model

    with _patch_discovery():
        assert resolve_local_model() == "ollama:qwen2.5:7b"


@override_settings(OFFLINE_LLM_MODEL="not-pulled:1b")
def test_resolve_local_model_falls_back_to_first_discovered():
    from apps.models_catalog.offline import resolve_local_model

    with _patch_discovery():
        assert resolve_local_model() == "ollama:qwen2.5:7b"


def test_resolve_local_model_hard_errors_with_no_cloud_fallback():
    from apps.models_catalog.offline import LocalModelUnavailable, resolve_local_model

    with _patch_discovery(models=[]):
        with pytest.raises(LocalModelUnavailable) as exc:
            resolve_local_model()
    assert "ollama pull" in str(exc.value)


# ---------------------------------------------------------------------------
# The offline-only "local" preset is not exposed on the online preset API — its
# <local> token needs a live Ollama probe that would 500 the endpoint.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_local_preset_absent_from_agents_endpoint(auth_client):
    resp = auth_client.get(reverse("agents-list"))
    assert resp.status_code == 200
    assert "local" not in resp.data["presets"]
    assert "hybrid" in resp.data["presets"]


@pytest.mark.django_db
def test_preset_detail_rejects_local_without_probing_ollama(auth_client):
    # Must 404 (not 500) even with no Ollama daemon — no discovery is attempted.
    with patch(
        "apps.models_catalog.ollama_discovery.discover_ollama_models", return_value=[]
    ):
        resp = auth_client.get(reverse("preset-detail", args=["local"]))
    assert resp.status_code == 404


def test_selectable_presets_excludes_local_but_keeps_the_rest():
    from apps.models_catalog.presets import PRESETS, SELECTABLE_PRESETS

    assert "local" in PRESETS  # still the internal expansion recipe
    assert "local" not in SELECTABLE_PRESETS
    assert set(SELECTABLE_PRESETS) == set(PRESETS) - {"local"}


def test_user_input_preset_validators_reject_local():
    # Every user-facing preset validator must reject the offline-only "local"
    # preset (it hard-errors on a live Ollama probe if it runs online).
    from rest_framework import serializers as drf

    from apps.portfolios.serializers import StrategySerializer
    from apps.schedules.serializers import ScheduledRunSerializer

    for cls in (StrategySerializer, ScheduledRunSerializer):
        ser = cls()
        with pytest.raises(drf.ValidationError):
            ser.validate_model_preset("local")
        assert ser.validate_model_preset("hybrid") == "hybrid"


# ---------------------------------------------------------------------------
# Provider HTTP fence (WS-1.3 / WS-1.8) — never construction, always the request.
# ---------------------------------------------------------------------------


def test_make_client_online_returns_real_httpx_client():
    from apps.data.providers._http import make_client

    with override_settings(OFFLINE_MODE=False):
        assert isinstance(make_client(timeout=30.0), httpx.Client)


def test_make_client_offline_blocks_every_request_verb():
    from apps.data.providers._http import _OfflineClient, make_client
    from apps.data.providers.errors import ProviderOffline

    with override_settings(OFFLINE_MODE=True):
        c = make_client(timeout=30.0)
        assert isinstance(c, _OfflineClient)
        for verb in ("get", "post", "put", "patch", "delete", "head"):
            with pytest.raises(ProviderOffline):
                getattr(c, verb)("https://example.com")


@pytest.mark.django_db
@override_settings(OFFLINE_MODE=True, FMP_API_KEY="x")
def test_provider_constructs_but_request_raises_provider_offline():
    """The run path needs a provider object; only an outbound call is fenced."""
    from apps.data.providers.errors import ProviderOffline
    from apps.data.providers.fmp import FmpProvider

    provider = FmpProvider(api_key="x")  # construction must succeed
    with pytest.raises(ProviderOffline):
        provider.get_daily_bars(
            "AAPL", dt.date(2026, 1, 1), dt.date(2026, 6, 1), as_of=dt.date(2026, 6, 1)
        )


# ---------------------------------------------------------------------------
# Registry LLM fence — the R3 hard guarantee (WS-1.6, D7).
# ---------------------------------------------------------------------------


@override_settings(OFFLINE_MODE=True)
def test_registry_fence_blocks_every_cloud_provider():
    """Covers every leak path: a per-agent override, the Settings default model,
    and a stored strategy preset all reduce to ``get_llm(<cloud provider>)``."""
    from hedgefund_agents.errors import OfflineLLMViolation
    from hedgefund_agents.registry import get_llm

    for provider in ("anthropic", "openrouter", "openai"):
        with pytest.raises(OfflineLLMViolation):
            get_llm(provider)


@override_settings(OFFLINE_MODE=True)
def test_registry_fence_allows_ollama():
    from hedgefund_agents.registry import get_llm

    client = get_llm("ollama")
    assert client.provider == "ollama"


@override_settings(OFFLINE_MODE=True)
def test_cloud_adapters_refuse_construction_offline():
    """Defense in depth (WS-1.8): even a direct construction is blocked."""
    from hedgefund_agents.errors import OfflineLLMViolation
    from hedgefund_agents.llm.adapters import AnthropicClient, OpenRouterClient

    for cls in (AnthropicClient, OpenRouterClient):
        with pytest.raises(OfflineLLMViolation):
            cls(api_key="x")


# ---------------------------------------------------------------------------
# Execution-seam preset forcing — all five creation paths (WS-1.5).
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(OFFLINE_MODE=True)
def test_cycle_seam_forces_local_over_everything(user):
    """Strategy cycle: `_resolve_model_overrides` forces local, beating even an
    explicit dispatch-modal override map."""
    from apps.portfolios.tasks import _resolve_model_overrides

    strategy = SimpleNamespace(user=user, model_preset="quality")
    with _patch_discovery():
        overrides = _resolve_model_overrides(
            strategy, preset="quality", overrides={"buffett": CLOUD}
        )
    assert overrides
    assert all(v.startswith("ollama:") for v in overrides.values())
    assert CLOUD not in overrides.values()


@pytest.mark.django_db
@override_settings(OFFLINE_MODE=True)
def test_execute_run_seam_forces_local(user):
    """Ad-hoc / scheduled-watchlist / rerun all execute via `execute_run`; its
    stored model_overrides (here a cloud slug) are replaced with local."""
    from apps.runs.models import Run
    from apps.runs.tasks import execute_run

    run = Run.objects.create(
        user=user,
        tickers=["AAPL"],
        as_of_date=dt.date(2026, 6, 1),
        status=Run.QUEUED,
        model_overrides={"buffett": CLOUD},
    )

    captured: dict = {}

    class _Stop(Exception):
        pass

    def _capture(state):
        captured.update(state)
        raise _Stop

    graph = MagicMock()
    graph.invoke.side_effect = _capture

    with patch("apps.runs.tasks.resolve_graph", return_value=graph), patch(
        "apps.runs.tasks.get_fmp_provider", return_value=MagicMock()
    ), patch("apps.runs.tasks.get_edgar_provider", return_value=MagicMock()), patch(
        "apps.runs.tasks.get_ownership_provider", return_value=MagicMock()
    ), _patch_discovery():
        with pytest.raises(_Stop):
            execute_run(run.id)

    mo = captured["model_overrides"]
    assert mo and all(v.startswith("ollama:") for v in mo.values())
    assert CLOUD not in mo.values()


@pytest.mark.django_db
@override_settings(OFFLINE_MODE=True, BACKTEST_PRIME_PARALLELISM=1)
def test_backtest_seam_forces_local(user):
    from apps.backtests.engine import prime_agent_cache
    from apps.backtests.models import Backtest

    bt = Backtest.objects.create(
        user=user,
        name="offline bt",
        universe=["AAA"],
        start_date=dt.date(2026, 1, 5),
        end_date=dt.date(2026, 1, 5),
        model_overrides={"buffett": CLOUD},
    )

    captured: dict = {}

    def _capture(state):
        captured.update(state)
        return {}

    graph = MagicMock()
    graph.invoke.side_effect = _capture
    day = dt.date(2026, 1, 5)

    with patch("apps.graphs.compiler.resolve_graph", return_value=graph), patch(
        "apps.data.providers.factory.get_fmp_provider", return_value=MagicMock()
    ), patch(
        "apps.data.providers.factory.get_edgar_provider", return_value=MagicMock()
    ), patch(
        "apps.data.providers.factory.get_ownership_provider", return_value=MagicMock()
    ), patch(
        "hedgefund_agents.versioning.ensure_versions_synced"
    ), patch(
        "hedgefund_agents.versioning.snapshot_versions", return_value={}
    ), patch(
        "apps.backtests.engine.trading_days", return_value=[day]
    ), patch(
        "apps.backtests.engine.rebalance_dates_for", return_value=[day]
    ), patch(
        # the prime thread pool recycles DB conns; would close the test txn.
        "django.db.close_old_connections"
    ), _patch_discovery():
        prime_agent_cache(bt=bt, start=day, end=day, rebalance_freq="daily")

    mo = captured["model_overrides"]
    assert mo and all(v.startswith("ollama:") for v in mo.values())
    assert CLOUD not in mo.values()


# ---------------------------------------------------------------------------
# Fenced views serve last-persisted DB rows, flagged stale (WS-1.3, R2).
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(OFFLINE_MODE=True)
def test_market_news_feed_serves_db_rows_stale_offline(auth_client):
    from apps.data.models import MarketNewsItem
    from apps.data.providers._dedup import dedup_key

    pub = timezone.now()
    MarketNewsItem.objects.create(
        provider="fmp",
        headline="Cached headline",
        summary="s",
        url="https://x.example/1",
        image_url="",
        source="Reuters",
        published_at=pub,
        symbols=[],
        tags=[],
        dedup_key=dedup_key("Cached headline", pub),
    )
    resp = auth_client.get(reverse("market-news-feed"))
    assert resp.status_code == 200  # never 500 at L1
    body = resp.json()
    assert body["stale"] is True
    assert any(i["headline"] == "Cached headline" for i in body["items"])


@pytest.mark.django_db
@override_settings(OFFLINE_MODE=True)
def test_macro_snapshot_serves_db_row_stale_offline(auth_client, user):
    from apps.data.models import MacroSnapshot

    MacroSnapshot.objects.create(
        as_of_date=dt.date(2026, 6, 1),
        growth_quadrant="expansion",
        inflation_regime="moderate",
        yield_curve_state="normal",
        policy_stance="neutral",
        narrative="n",
        sector_implications={},
        series_used={},
        markov_consensus={},
    )
    resp = auth_client.get(reverse("macro-snapshot") + "?as_of=2026-06-02")
    assert resp.status_code == 200
    assert resp.json()["stale"] is True


# ---------------------------------------------------------------------------
# Celery no-ops (WS-1.7) — outward-facing beats skip; runs still execute.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(OFFLINE_MODE=True)
@pytest.mark.parametrize(
    "dotted,expect_key",
    [
        ("apps.data.tasks.prewarm_macro_snapshot", None),
        ("apps.data.tasks.ingest_13f_current_quarter", None),
        ("apps.brokers.tasks.poll_open_orders", "status"),
        ("apps.brokers.tasks.reconcile_all_accounts", "status"),
        ("apps.brokers.tasks.keep_ibkr_gateway_warm", "status"),
        ("apps.models_catalog.tasks.reconcile_model_catalog", "status"),
        ("apps.persona_evolution.tasks.evolve_personas", "reason"),
        ("apps.portfolios.tasks_lab.fetch_lab_news", "status"),
        ("apps.portfolios.tasks_lab.run_news_lab_cycles", "status"),
    ],
)
def test_outward_facing_tasks_noop_offline(dotted, expect_key):
    import importlib

    module_path, func_name = dotted.rsplit(".", 1)
    func = getattr(importlib.import_module(module_path), func_name)
    result = func()
    if expect_key == "status":
        assert result == {"status": "skipped_offline"}
    elif expect_key == "reason":
        assert result.get("reason") == "offline"
    else:
        assert result == "skipped_offline"


@pytest.mark.django_db
@override_settings(OFFLINE_MODE=True)
def test_notifications_skipped_offline(user):
    from apps.notifications.channels.email import send_email
    from apps.notifications.channels.telegram import send_telegram

    channel = SimpleNamespace(user=user, config={"address": "a@b.com"})
    ok, err = send_email(channel, "s", "b")
    assert ok is False and "offline" in err
    channel2 = SimpleNamespace(
        user=user, config={"bot_token": "t", "chat_id": "1"}
    )
    ok2, err2 = send_telegram(channel2, "s", "b")
    assert ok2 is False and "offline" in err2


# ---------------------------------------------------------------------------
# Socket guard (WS-1.8) — zero external HTTP under OFFLINE_MODE.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(OFFLINE_MODE=True, FMP_API_KEY="x", FRED_API_KEY="x", TIINGO_API_KEY="x")
def test_socket_guard_no_external_http_from_providers():
    """Patch httpx.Client.send to detonate — an offline provider must never
    reach it (it serves the offline stand-in), so no external socket opens."""
    from apps.data.providers.errors import ProviderOffline
    from apps.data.providers.fmp import FmpProvider
    from apps.data.providers.fred import FredProvider

    def _boom(*a, **k):  # pragma: no cover — asserts it's never called
        raise AssertionError("external HTTP attempted under OFFLINE_MODE")

    with patch("httpx.Client.send", _boom):
        with pytest.raises(ProviderOffline):
            FmpProvider(api_key="x").get_latest_quote("AAPL")
        with pytest.raises(ProviderOffline):
            FredProvider(api_key="x").get_latest_value("GDP", as_of=dt.date(2026, 1, 1))
