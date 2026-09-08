"""Adversarial review (reviewer: data) — news providers, feed, sentiment/translation.

Proof tests for:
  * F-NEWS-FRESH-DEAD: ``NewsService.fetch_and_persist`` compares provider
    names ("fmp"/"tiingo") against ``NewsItem.source`` — which holds the
    PUBLISHER ("Reuters", "seekingalpha.com") — so the freshness
    short-circuit never fires with real rows: every council/news call re-hits
    both providers.
  * F-MKTNEWS-FAILFIRST: ``MarketNewsFmpProvider.fetch_latest`` re-raises on
    the first sub-feed failure, discarding the second sub-feed entirely (the
    inline comment claims the opposite).
  * F-NEWS-LLM-PLATFORM: sentiment + translation ran on the PLATFORM
    OpenRouter key for any user without a BYO key, with ANY active catalog
    model selectable (frontier/reasoning included) and no spend guard
    (``record_llm_call(run_id=None)`` → budget enforcement skipped).
    FIXED in WAVE-3 P2: BYOK-required (unless ``ALLOW_PLATFORM_LLM_FOR_NEWS``),
    frugal-only model menu without a key, and a per-user daily USD cap.
  * F-FEED-REFETCH-ON-OUTAGE: when every provider fails, no row gets a fresh
    ``fetched_at`` so every subsequent (non-forced) page view re-hits the
    failing providers.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.data.models import MarketNewsItem, NewsItem
from apps.data.providers.market_news import MarketNewsService
from apps.data.providers.market_news_fmp import MarketNewsFmpProvider
from apps.data.providers.news import NewsService

pytestmark = pytest.mark.django_db

User = get_user_model()


# ---------------------------------------------------------------------------
# F-NEWS-FRESH-DEAD
# ---------------------------------------------------------------------------


class _Tracking:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    def fetch(self, ticker, *, as_of, lookback_days=30):
        self.calls += 1
        return []


def test_news_freshness_short_circuit_fires_on_provider_not_publisher():
    """FIXED: the short-circuit compares ``NewsItem.provider`` (the adapter)
    instead of ``source`` (the publisher), so a fresh cache really does skip
    the network."""
    # Must track the rows below, which are stamped `timezone.now()`: the cache
    # lookup filters `published_at__date__lte=as_of`, so a pinned as_of silently
    # excludes them the day after it — the short-circuit then cannot fire and
    # this test fails on a date rollover rather than on a real regression.
    as_of = timezone.now().date()
    for prov, publisher, i in (("tiingo", "Reuters", 1), ("fmp", "seekingalpha.com", 2)):
        NewsItem.objects.create(
            ticker="AAPL",
            published_at=timezone.now(),  # within the 1-day freshness window
            headline=f"fresh {i}", source=publisher, provider=prov,
            url=f"https://x/{i}", dedup_key=f"k{i}",
        )
    t, f = _Tracking("tiingo"), _Tracking("fmp")
    svc = NewsService(tiingo=None, fmp=None)
    svc._providers = [t, f]
    rows = svc.fetch_and_persist("AAPL", as_of=as_of)
    assert (t.calls, f.calls) == (0, 0)
    assert len(rows) == 2

    # A provider with NO cached row still gets called (the short-circuit only
    # fires when every configured provider is represented).
    t2, f2, extra = _Tracking("tiingo"), _Tracking("fmp"), _Tracking("benzinga")
    svc2 = NewsService(tiingo=None, fmp=None)
    svc2._providers = [t2, f2, extra]
    svc2.fetch_and_persist("AAPL", as_of=as_of)
    assert (t2.calls, f2.calls, extra.calls) == (1, 1, 1)


# ---------------------------------------------------------------------------
# F-MKTNEWS-FAILFIRST
# ---------------------------------------------------------------------------


def test_fmp_market_news_first_subfeed_failure_keeps_second_subfeed():
    """FIXED: a failing sub-feed is warned about, not fatal — the other
    sub-feed's rows still reach the aggregator."""
    stock_rows = [{
        "title": "Stock story", "url": "https://s/1", "text": "t",
        "publishedDate": timezone.now().strftime("%Y-%m-%d %H:%M:%S"), "symbol": "AAPL",
    }]

    def _get(url, params=None):
        resp = MagicMock()
        if url.endswith("/news/general-latest"):
            req = httpx.Request("GET", url)
            resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "402", request=req, response=httpx.Response(402, request=req)
            )
        else:
            resp.raise_for_status.return_value = None
            resp.json.return_value = stock_rows
        return resp

    http = MagicMock()
    http.get.side_effect = _get
    prov = MarketNewsFmpProvider(api_key="k", http=http)
    out = prov.fetch_latest(limit=10)
    # Both sub-feeds are requested; the survivor's rows come back.
    assert [c.args[0] for c in http.get.call_args_list] == [
        "https://financialmodelingprep.com/stable/news/general-latest",
        "https://financialmodelingprep.com/stable/news/stock-latest",
    ]
    assert [i.headline for i in out] == ["Stock story"]
    assert prov.warnings == [
        "fmp:/news/general-latest: HTTPStatusError"
    ]
    # And the aggregator persists what it got, surfacing the partial failure.
    svc = MarketNewsService(fmp=prov, tiingo=None)
    rows = svc.fetch_latest(force=True)
    assert [r.headline for r in rows] == ["Stock story"]
    assert svc.warnings == ["fmp:/news/general-latest: HTTPStatusError"]


def test_fmp_market_news_both_subfeeds_failing_is_still_a_provider_outage():
    def _get(url, params=None):
        resp = MagicMock()
        req = httpx.Request("GET", url)
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "402", request=req, response=httpx.Response(402, request=req)
        )
        return resp

    http = MagicMock()
    http.get.side_effect = _get
    prov = MarketNewsFmpProvider(api_key="k", http=http)
    with pytest.raises(httpx.HTTPStatusError):
        prov.fetch_latest(limit=10)
    svc = MarketNewsService(fmp=prov, tiingo=None)
    assert svc.fetch_latest(force=True) == []
    assert svc.warnings == ["fmp: HTTPStatusError"]


# ---------------------------------------------------------------------------
# F-NEWS-LLM-PLATFORM
# ---------------------------------------------------------------------------


def test_any_active_catalog_model_is_selectable_and_runs_on_platform_key(monkeypatch):
    """FIXED: news sentiment/translation are BYOK-only.

    Without the user's own OpenRouter key the frontier model cannot even be
    SELECTED (the picker is restricted to the frugal preset menu), and the
    classifier refuses to run rather than reaching for the platform key — the
    feed still renders, with a reason.
    """
    from apps.models_catalog.models import ModelEntry

    ModelEntry.objects.create(
        id="openrouter:openai/o1-pro", provider="openrouter", display_name="o1-pro",
        tier="frontier", supports_reasoning=True,
        price_in_per_mtok="150", price_out_per_mtok="600", is_active=True,
    )
    user = User.objects.create_user(email="n1@x.com", password="x" * 12)  # no ProviderKey
    client = APIClient()
    client.force_authenticate(user)
    resp = client.put(
        "/api/news/preferences/",
        {"sentiment_model": "openrouter:openai/o1-pro",
         "translation_model": "openrouter:openai/o1-pro"},
        format="json",
    )
    assert resp.status_code == 400, resp.content
    assert "own OpenRouter key" in resp.json()["detail"]

    # Even called directly with that model, the classifier never builds a
    # client: no BYO key means no platform-key fallback.
    built: list[tuple[str, int | None, str]] = []

    def fake_make_client(provider, user_id, api_key, host):
        built.append((provider, user_id, api_key))
        return object()

    monkeypatch.setattr("hedgefund_agents.registry._make_client", fake_make_client)

    from apps.data.market_news_sentiment import MarketNewsSentimentBatch, classify

    def fake_call_structured(client, *, model, schema, messages, **kw):
        raise AssertionError("no LLM call may be made without a user key")

    monkeypatch.setattr("hedgefund_agents.llm.structured.call_structured", fake_call_structured)
    monkeypatch.setattr("hedgefund_agents._persist.record_llm_call", lambda **kw: None)

    row = MarketNewsItem.objects.create(
        provider="fmp", headline="h", url="https://u/1", published_at=timezone.now(),
    )
    ok, warn = classify([row], model_id="openrouter:openai/o1-pro", user_id=user.id)
    assert ok is False
    assert warn is not None and "/settings/providers" in warn
    assert built == []

    # With the user's own key the same call runs — on THEIR credit.
    from apps.models_catalog.models import ProviderKey

    pk = ProviderKey.objects.create(user=user)
    pk.set_key("openrouter", "sk-or-user-key")
    pk.save()

    def ok_call_structured(client, *, model, schema, messages, **kw):
        assert model == "openai/o1-pro"
        return MarketNewsSentimentBatch(items=[]), MagicMock(usage=None, cost_usd=0)

    monkeypatch.setattr("hedgefund_agents.llm.structured.call_structured", ok_call_structured)
    ok, warn = classify([row], model_id="openrouter:openai/o1-pro", user_id=user.id)
    assert ok is True, warn
    assert built == [("openrouter", user.id, "sk-or-user-key")]

    # ... and the frontier model is now selectable too.
    resp = client.put(
        "/api/news/preferences/",
        {"sentiment_model": "openrouter:openai/o1-pro"},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["preferences"]["sentiment_model"] == "openrouter:openai/o1-pro"


# ---------------------------------------------------------------------------
# F-FEED-REFETCH-ON-OUTAGE
# ---------------------------------------------------------------------------


def test_feed_rehits_failing_providers_on_every_page_view():
    class _Broken:
        name = "fmp"
        calls = 0

        def fetch_latest(self, *, limit=60):
            _Broken.calls += 1
            raise httpx.ConnectError("boom")

    svc = MarketNewsService(fmp=_Broken(), tiingo=None)
    for _ in range(5):
        svc.fetch_latest(force=False)
    # No fresh row was persisted, so the 15-minute short-circuit never engages.
    assert _Broken.calls == 5
