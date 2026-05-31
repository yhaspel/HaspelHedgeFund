"""Tests for the news auto-translation feature (P4-pre-news-xlate)."""
from __future__ import annotations

import datetime as dt

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.data.models import MarketNewsItem, UserNewsPreferences
from apps.data.providers._dedup import dedup_key
from apps.data.providers._language import detect_language

pytestmark = pytest.mark.django_db


def _make_item(**kw) -> MarketNewsItem:
    defaults = dict(
        provider="fmp",
        headline="Apple beats Q3 estimates",
        summary="Strong iPhone sales.",
        url="https://example.com/a",
        image_url="",
        source="FMP",
        published_at=timezone.now(),
        symbols=["AAPL"],
        tags=[],
    )
    defaults.update(kw)
    return MarketNewsItem.objects.create(**defaults)


def _make_user(username="u1"):
    User = get_user_model()
    return User.objects.create_user(
        email=f"{username}@example.com", password="x"
    )


# --- Clustering fix (_dedup.py) -------------------------------------------

def test_dedup_distinct_non_latin_headlines_get_distinct_keys() -> None:
    d = dt.datetime(2026, 5, 31, 12, 0, 0)
    a = dedup_key("巴中关系迎来新篇章", d)
    b = dedup_key("巴基斯坦與中國的關係迎來嶄新篇章", d)
    assert a != b


def test_dedup_identical_foreign_headlines_still_match() -> None:
    d = dt.datetime(2026, 5, 31, 12, 0, 0)
    assert dedup_key("巴中关系迎来新篇章", d) == dedup_key("巴中关系迎来新篇章", d)


def test_dedup_english_key_unchanged_byte_for_byte() -> None:
    d = dt.datetime(2026, 5, 31, 12, 0, 0)
    # Pre-change value computed from the original implementation.
    assert dedup_key("Apple beats Q3 estimates", d) == "28e22a229e11cd0ac3108b6de2a063ed"


# --- Language detection (_language.py) ------------------------------------

def test_detect_language_short_cjk_via_fast_path() -> None:
    assert detect_language("巴中关系迎来新篇章").startswith("zh") or detect_language(
        "巴中关系迎来新篇章"
    ) == "und-nonlatin"


def test_detect_language_hebrew_is_foreign() -> None:
    assert detect_language("פרק חדש מתפתח ביחסי פקיסטן-סין") != ""


def test_detect_language_latin_european() -> None:
    assert detect_language(
        "La banque centrale européenne relève ses taux directeurs aujourd hui"
    ) == "fr"


def test_detect_language_accented_english_not_flagged() -> None:
    # A clearly-English headline with a single accented word must not be flagged
    # (the LATIN_CONF gate biases toward not translating real English).
    assert detect_language(
        "Apple and the café chain reported higher revenue and raised "
        "their full-year outlook today"
    ) == ""


def test_detect_language_short_english_not_flagged() -> None:
    assert detect_language("Apple beats estimates") == ""


def test_detect_language_deterministic() -> None:
    s = "Die Deutsche Bank meldet einen starken Quartalsgewinn heute Morgen"
    assert detect_language(s) == detect_language(s)


# --- translate() ----------------------------------------------------------

def _patch_translate(monkeypatch, *, primary_ok=True, fallback_ok=True, calls=None):
    """Patch the LLM plumbing. ``calls`` accumulates the models actually hit."""
    from apps.data.market_news_translation import (
        MarketNewsTranslationBatch,
        NewsTranslationItem,
    )

    if calls is None:
        calls = []
    record = {"count": 0}

    def fake_get_llm(provider, *a, **k):
        return object()

    def make_call(ok):
        def fake_call_structured(client, *, model, schema, messages, **kw):
            calls.append(model)
            if not ok:
                raise RuntimeError("model failed")
            # Echo English translations for each numbered idx in the prompt.
            user = messages[-1].content
            n = user.count("HEADLINE:")
            items_out = [
                NewsTranslationItem(idx=i, headline=f"EN headline {i}", summary="EN sum")
                for i in range(n)
            ]
            resp = type("R", (), {"usage": None, "cost_usd": 0.0})()
            return MarketNewsTranslationBatch(items=items_out), resp

        return fake_call_structured

    # Route per-model success: primary id contains 'qwen', fallback 'llama'.
    def dispatch(client, *, model, **kw):
        is_primary = "qwen" in model
        ok = primary_ok if is_primary else fallback_ok
        return make_call(ok)(client, model=model, **kw)

    monkeypatch.setattr("hedgefund_agents.registry.get_llm", fake_get_llm)
    monkeypatch.setattr(
        "hedgefund_agents.llm.structured.call_structured", dispatch
    )

    def fake_record(**k):
        record["count"] += 1

    monkeypatch.setattr("hedgefund_agents._persist.record_llm_call", fake_record)
    return calls, record


def test_translate_sends_only_foreign_untranslated(monkeypatch) -> None:
    from apps.data.market_news_translation import translate

    english = _make_item(url="https://example.com/en", language="en")
    foreign = _make_item(url="https://example.com/zh", language="zh-cn",
                         headline="巴中关系迎来新篇章")
    already = _make_item(url="https://example.com/done", language="fr",
                        translated_from="fr", headline_en="done")
    calls, record = _patch_translate(monkeypatch)

    ok, warning = translate(
        [english, foreign, already],
        model_id="openrouter:qwen/q",
        fallback_model_id="openrouter:meta-llama/l",
        user_id=None,
    )
    assert ok is True and warning is None
    assert len(calls) == 1  # one batched call
    foreign.refresh_from_db()
    assert foreign.translated_from == "zh-cn"
    assert foreign.headline_en == "EN headline 0"
    assert record["count"] == 1


def test_translate_empty_targets_no_call(monkeypatch) -> None:
    from apps.data.market_news_translation import translate

    english = _make_item(url="https://example.com/en", language="en")
    calls, record = _patch_translate(monkeypatch)
    ok, warning = translate(
        [english], model_id="openrouter:qwen/q",
        fallback_model_id="openrouter:meta-llama/l", user_id=None,
    )
    assert ok is True and warning is None
    assert calls == []
    assert record["count"] == 0


def test_translate_fallback_chain(monkeypatch) -> None:
    from apps.data.market_news_translation import translate

    foreign = _make_item(url="https://example.com/zh", language="zh-cn",
                        headline="巴中关系迎来新篇章")
    calls, record = _patch_translate(monkeypatch, primary_ok=False, fallback_ok=True)
    ok, warning = translate(
        [foreign], model_id="openrouter:qwen/q",
        fallback_model_id="openrouter:meta-llama/l", user_id=None,
    )
    assert ok is True
    foreign.refresh_from_db()
    assert foreign.translated_from == "zh-cn"
    assert foreign.translation_model == "openrouter:meta-llama/l"
    assert record["count"] == 1  # only the succeeding (fallback) attempt
    assert len(calls) == 2  # primary tried, then fallback


def test_translate_double_failure_leaves_untranslated(monkeypatch) -> None:
    from apps.data.market_news_translation import translate

    foreign = _make_item(url="https://example.com/zh", language="zh-cn",
                        headline="巴中关系迎来新篇章")
    calls, record = _patch_translate(monkeypatch, primary_ok=False, fallback_ok=False)
    ok, warning = translate(
        [foreign], model_id="openrouter:qwen/q",
        fallback_model_id="openrouter:meta-llama/l", user_id=None,
    )
    assert ok is False
    assert warning
    foreign.refresh_from_db()
    assert foreign.translated_from == ""
    assert record["count"] == 0


def test_translate_empty_returned_headline_left_untranslated(monkeypatch) -> None:
    from apps.data.market_news_translation import (
        MarketNewsTranslationBatch,
        NewsTranslationItem,
        translate,
    )

    foreign = _make_item(url="https://example.com/zh", language="zh-cn",
                        headline="巴中关系迎来新篇章")

    def fake_call(client, *, model, schema, messages, **kw):
        out = [NewsTranslationItem(idx=0, headline="", summary="")]
        resp = type("R", (), {"usage": None, "cost_usd": 0.0})()
        return MarketNewsTranslationBatch(items=out), resp

    monkeypatch.setattr("hedgefund_agents.registry.get_llm", lambda *a, **k: object())
    monkeypatch.setattr("hedgefund_agents.llm.structured.call_structured", fake_call)
    monkeypatch.setattr("hedgefund_agents._persist.record_llm_call", lambda **k: None)

    ok, warning = translate(
        [foreign], model_id="openrouter:qwen/q",
        fallback_model_id="openrouter:meta-llama/l", user_id=None,
    )
    assert ok is True
    foreign.refresh_from_db()
    assert foreign.translated_from == ""  # empty translation not tagged


# --- Feed endpoint --------------------------------------------------------

def _patch_service(monkeypatch):
    """Avoid any real provider/network fetch; return persisted rows."""
    def fake_service(user):
        class _Svc:
            warnings: list = []
            providers_used = ["fmp"]

            def fetch_latest(self, force=False):
                return list(MarketNewsItem.objects.all())

        return _Svc()

    monkeypatch.setattr(
        "apps.data.providers.factory.get_market_news_service", fake_service
    )


def test_feed_translates_foreign_rep(monkeypatch) -> None:
    user = _make_user()
    _make_item(url="https://example.com/zh", language="zh-cn",
              headline="巴中关系迎来新篇章", summary="中文摘要")
    prefs = UserNewsPreferences.objects.create(
        user=user, sentiment_enabled=False, translation_enabled=True
    )
    _patch_translate(monkeypatch)
    _patch_service(monkeypatch)

    client = APIClient()
    client.force_authenticate(user=user)
    resp = client.get("/api/news/feed/")
    assert resp.status_code == 200
    item = resp.data["items"][0]
    assert item["headline"] == "EN headline 0"
    assert item["translated_from"] == "zh-cn"
    assert item["original_headline"] == "巴中关系迎来新篇章"


def test_feed_translation_off_no_calls(monkeypatch) -> None:
    user = _make_user()
    _make_item(url="https://example.com/zh", language="zh-cn",
              headline="巴中关系迎来新篇章")
    UserNewsPreferences.objects.create(
        user=user, sentiment_enabled=False, translation_enabled=False
    )
    calls, record = _patch_translate(monkeypatch)
    _patch_service(monkeypatch)

    client = APIClient()
    client.force_authenticate(user=user)
    resp = client.get("/api/news/feed/")
    assert resp.status_code == 200
    assert calls == []
    item = resp.data["items"][0]
    assert item["headline"] == "巴中关系迎来新篇章"
    assert item["translated_from"] is None


def test_feed_detects_language_for_preexisting_rows(monkeypatch) -> None:
    user = _make_user()
    # language="" — a pre-existing row; detection should populate it.
    row = _make_item(url="https://example.com/zh", language="",
                    headline="巴中关系迎来新篇章")
    UserNewsPreferences.objects.create(
        user=user, sentiment_enabled=False, translation_enabled=True
    )
    _patch_translate(monkeypatch)
    _patch_service(monkeypatch)

    client = APIClient()
    client.force_authenticate(user=user)
    resp = client.get("/api/news/feed/")
    assert resp.status_code == 200
    row.refresh_from_db()
    assert row.language and row.language != "en"


# --- Preferences endpoint -------------------------------------------------

def test_prefs_get_returns_translation_fields_and_choices() -> None:
    user = _make_user()
    client = APIClient()
    client.force_authenticate(user=user)
    resp = client.get("/api/news/preferences/")
    assert resp.status_code == 200
    prefs = resp.data["preferences"]
    assert "translation_enabled" in prefs
    assert prefs["translation_model"] == "openrouter:qwen/qwen3-235b-a22b-2507"
    assert prefs["translation_fallback_model"] == (
        "openrouter:meta-llama/llama-3.3-70b-instruct"
    )
    assert "translation_model_choices" in resp.data


def test_prefs_put_rejects_non_allowed_translation_model() -> None:
    user = _make_user()
    client = APIClient()
    client.force_authenticate(user=user)
    resp = client.put(
        "/api/news/preferences/",
        {"translation_model": "openai:gpt-4o"},
        format="json",
    )
    assert resp.status_code == 400


def test_prefs_put_accepts_allowed_translation_model() -> None:
    user = _make_user()
    client = APIClient()
    client.force_authenticate(user=user)
    resp = client.put(
        "/api/news/preferences/",
        {
            "translation_model": "openrouter:meta-llama/llama-3.3-70b-instruct",
            "translation_enabled": False,
        },
        format="json",
    )
    assert resp.status_code == 200
    assert resp.data["preferences"]["translation_enabled"] is False
