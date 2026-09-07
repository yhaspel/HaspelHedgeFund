"""WAVE-3 P2 item 1 — News LLM cost policy (BYOK-required + daily cap).

Covers the parts of the policy the flipped proof test
(``test_review_data_news.py::test_any_active_catalog_model_is_selectable_and_runs_on_platform_key``)
does not: the daily USD cap, the operator opt-in, the ``llm_status`` block on
both endpoints, and the "a model switch must not re-score" rule.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.data.models import MarketNewsItem, NewsLlmUsage, UserNewsPreferences

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def catalog(db):
    from apps.models_catalog.models import ModelEntry

    ModelEntry.objects.update_or_create(
        id="openrouter:qwen/qwen3.6-27b",
        defaults=dict(
            provider="openrouter", display_name="Qwen3.6 27B", tier="hosted_open",
            supports_reasoning=True, price_in_per_mtok="0.1",
            price_out_per_mtok="0.3", is_active=True,
        ),
    )
    ModelEntry.objects.update_or_create(
        id="openrouter:meta-llama/llama-3.3-70b-instruct",
        defaults=dict(
            provider="openrouter", display_name="Llama 3.3 70B", tier="hosted_open",
            supports_reasoning=False, price_in_per_mtok="0.2",
            price_out_per_mtok="0.5", is_active=True,
        ),
    )
    ModelEntry.objects.update_or_create(
        id="anthropic:claude-haiku-4-5-20251001",
        defaults=dict(
            provider="anthropic", display_name="Haiku 4.5", tier="frontier",
            supports_reasoning=False, price_in_per_mtok="1",
            price_out_per_mtok="5", is_active=True,
        ),
    )


def _user(email="w3p2@x.com", *, with_key=False):
    u = User.objects.create_user(email=email, password="x" * 12)
    if with_key:
        from apps.models_catalog.models import ProviderKey

        pk = ProviderKey.objects.create(user=u)
        pk.set_key("openrouter", "sk-or-user")
        pk.save()
    return u


def _row(**kw) -> MarketNewsItem:
    defaults = dict(
        provider="fmp", headline="h", url="https://u/1",
        published_at=timezone.now(),
    )
    defaults.update(kw)
    return MarketNewsItem.objects.create(**defaults)


def _patch_llm(monkeypatch, *, cost_usd=0.01):
    """Make the LLM plumbing succeed and report ``cost_usd`` per call."""
    from apps.data.market_news_sentiment import MarketNewsSentimentBatch, NewsSentimentItem

    calls: list[str] = []

    def fake_call_structured(client, *, model, schema, messages, **kw):
        calls.append(model)
        user_prompt = messages[-1].content
        n = user_prompt.count("HEADLINE:")
        return (
            MarketNewsSentimentBatch(
                items=[
                    NewsSentimentItem(idx=i, label="neutral", score=0.0, rationale="x")
                    for i in range(n)
                ]
            ),
            MagicMock(usage=None, cost_usd=cost_usd, prior_attempts=[]),
        )

    monkeypatch.setattr("hedgefund_agents.registry._make_client", lambda *a, **k: object())
    monkeypatch.setattr(
        "hedgefund_agents.llm.structured.call_structured", fake_call_structured
    )
    monkeypatch.setattr("hedgefund_agents._persist.record_llm_call", lambda **kw: None)
    return calls


# ---------------------------------------------------------------------------
# BYOK gate
# ---------------------------------------------------------------------------


def test_no_key_skips_sentiment_with_a_reason_and_the_feed_still_renders(
    monkeypatch, catalog
):
    user = _user()
    UserNewsPreferences.objects.create(user=user, sentiment_enabled=True)
    _row(headline="Market rallies")
    calls = _patch_llm(monkeypatch)

    def fake_service(user=None):
        class _Svc:
            warnings: list = []
            providers_used = ["fmp"]

            def fetch_latest(self, force=False):
                return list(MarketNewsItem.objects.all())

        return _Svc()

    monkeypatch.setattr(
        "apps.data.providers.factory.get_market_news_service", fake_service
    )
    client = APIClient()
    client.force_authenticate(user)
    resp = client.get("/api/news/feed/")
    assert resp.status_code == 200
    body = resp.json()
    # The story is served, unscored, with the reason on BOTH surfaces.
    assert len(body["items"]) == 1
    assert body["items"][0]["sentiment"] is None
    assert "/settings/providers" in body["sentiment_warning"]
    assert body["sentiment_warning"] in body["warnings"]
    assert body["llm_status"]["allowed"] is False
    assert body["llm_status"]["byok_required"] is True
    assert body["llm_status"]["has_user_key"] is False
    assert body["llm_status"]["model_choices_restricted"] is True
    assert calls == []  # no LLM call was made


@override_settings(ALLOW_PLATFORM_LLM_FOR_NEWS=True)
def test_operator_opt_in_allows_the_platform_key(monkeypatch, catalog):
    user = _user()
    row = _row()
    calls = _patch_llm(monkeypatch)
    from apps.data.market_news_sentiment import classify

    ok, warn = classify([row], model_id="openrouter:qwen/qwen3.6-27b", user_id=user.id)
    assert (ok, warn) == (True, None)
    assert calls == ["qwen/qwen3.6-27b"]


# ---------------------------------------------------------------------------
# Daily cap
# ---------------------------------------------------------------------------


@override_settings(NEWS_LLM_DAILY_CAP_USD="0.50")
def test_daily_cap_skips_once_todays_spend_is_over(monkeypatch, catalog):
    user = _user(with_key=True)
    calls = _patch_llm(monkeypatch, cost_usd=0.60)
    from apps.data.market_news_sentiment import classify

    # First call runs and is attributed to the user.
    ok, warn = classify([_row()], model_id="openrouter:qwen/qwen3.6-27b", user_id=user.id)
    assert (ok, warn) == (True, None)
    usage = NewsLlmUsage.objects.get(user=user)
    assert usage.feature == "sentiment"
    assert usage.cost_usd == Decimal("0.600000")

    # The next one is over the cap: skipped with a reason, never raised.
    ok, warn = classify(
        [_row(url="https://u/2")], model_id="openrouter:qwen/qwen3.6-27b",
        user_id=user.id,
    )
    assert ok is False
    assert "budget" in warn and "0.50" in warn
    assert calls == ["qwen/qwen3.6-27b"]  # only the first one

    # Another user is unaffected — the cap is per user.
    other = _user(email="other@x.com", with_key=True)
    ok, _warn = classify(
        [_row(url="https://u/3")], model_id="openrouter:qwen/qwen3.6-27b",
        user_id=other.id,
    )
    assert ok is True


@override_settings(NEWS_LLM_DAILY_CAP_USD="0.50")
def test_daily_cap_also_covers_translation(monkeypatch, catalog):
    user = _user(with_key=True)
    NewsLlmUsage.objects.create(user=user, feature="sentiment", cost_usd=Decimal("0.75"))
    from apps.data.market_news_translation import translate

    foreign = _row(language="zh-cn", headline="巴中关系")
    ok, warn = translate(
        [foreign], model_id="openrouter:qwen/qwen3.6-27b",
        fallback_model_id="openrouter:meta-llama/llama-3.3-70b-instruct",
        user_id=user.id,
    )
    assert ok is False
    assert "Translation" in warn and "budget" in warn


# ---------------------------------------------------------------------------
# A model switch must not re-score
# ---------------------------------------------------------------------------


@override_settings(ALLOW_PLATFORM_LLM_FOR_NEWS=True)
def test_switching_the_model_does_not_rescore_already_scored_rows(monkeypatch, catalog):
    user = _user()
    scored = _row()
    calls = _patch_llm(monkeypatch)
    from apps.data.market_news_sentiment import classify

    classify([scored], model_id="openrouter:qwen/qwen3.6-27b", user_id=user.id)
    scored.refresh_from_db()
    assert scored.sentiment_model == "openrouter:qwen/qwen3.6-27b"

    # Switch the picker: the already-scored row is NOT re-sent; only the new
    # unscored row is.
    fresh = _row(url="https://u/new")
    ok, warn = classify(
        [scored, fresh],
        model_id="openrouter:meta-llama/llama-3.3-70b-instruct",
        user_id=user.id,
    )
    assert (ok, warn) == (True, None)
    assert calls == ["qwen/qwen3.6-27b", "meta-llama/llama-3.3-70b-instruct"]
    scored.refresh_from_db()
    assert scored.sentiment_model == "openrouter:qwen/qwen3.6-27b"  # untouched
    fresh.refresh_from_db()
    assert fresh.sentiment_model == "openrouter:meta-llama/llama-3.3-70b-instruct"


# ---------------------------------------------------------------------------
# /news/preferences/ contract
# ---------------------------------------------------------------------------


def test_preferences_marks_which_choices_are_selectable(catalog):
    user = _user()
    client = APIClient()
    client.force_authenticate(user)
    body = client.get("/api/news/preferences/").json()
    by_id = {c["id"]: c for c in body["sentiment_model_choices"]}
    assert by_id["openrouter:qwen/qwen3.6-27b"]["selectable"] is True
    assert by_id["anthropic:claude-haiku-4-5-20251001"]["selectable"] is False
    assert body["llm_status"]["model_choices_restricted"] is True
    assert body["llm_status"]["daily_cap_usd"] == pytest.approx(0.50)
    assert body["llm_status"]["spent_today_usd"] == pytest.approx(0.0)


def test_preferences_frugal_model_is_always_allowed(catalog):
    user = _user()
    client = APIClient()
    client.force_authenticate(user)
    resp = client.put(
        "/api/news/preferences/",
        {"translation_model": "openrouter:meta-llama/llama-3.3-70b-instruct"},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["preferences"]["translation_model"] == (
        "openrouter:meta-llama/llama-3.3-70b-instruct"
    )


def test_preferences_rejects_nonfrugal_translation_model_without_a_key(catalog):
    user = _user()
    client = APIClient()
    client.force_authenticate(user)
    resp = client.put(
        "/api/news/preferences/",
        {"translation_model": "anthropic:claude-haiku-4-5-20251001"},
        format="json",
    )
    assert resp.status_code == 400
    assert "own OpenRouter key" in resp.json()["detail"]
