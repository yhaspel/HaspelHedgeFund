"""Tests for the P3-prereq-4 market-news stack.

Covers the dedup helper extraction (regression guard), the provider adapters,
the aggregator service, the interest-ranking function, the sentiment-classifier
batching contract, the model allow-list, the feed + preferences endpoints, and
the point-in-time import boundary.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import re
from decimal import Decimal
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.data.market_news_rank import (
    BREADTH_CAP,
    SOURCE_WEIGHTS,
    rank_feed,
)
from apps.data.market_news_sentiment import (
    MarketNewsSentimentBatch,
    NewsSentimentItem,
    classify,
    frugal_sentiment_models,
    is_allowed_sentiment_model,
)
from apps.data.models import MarketNewsItem, UserNewsPreferences
from apps.data.providers._dedup import dedup_key
from apps.data.providers.market_news import (
    RETENTION_DAYS,
    MarketNewsService,
)
from apps.data.providers.market_news_fmp import MarketNewsFmpProvider
from apps.data.providers.market_news_tiingo import MarketNewsTiingoProvider

User = get_user_model()


# Provider fixtures must use timestamps inside the rolling retention window
# (now - RETENTION_DAYS); hardcoded dates were a time bomb that passed on
# 2026-05-29 and broke once the wall-clock rolled to 2026-05-30. These helpers
# keep fixtures a fixed, in-window age regardless of when the suite runs.
def _recent_iso(days_ago: int = 1, hour: int = 9) -> str:
    d = (dt.datetime.now(dt.UTC) - dt.timedelta(days=days_ago)).replace(
        hour=hour, minute=0, second=0, microsecond=0,
    )
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def _recent_space(days_ago: int = 1, hour: int = 8) -> str:
    """`_recent_iso` in the space-separated format some feeds emit."""
    d = (dt.datetime.now(dt.UTC) - dt.timedelta(days=days_ago)).replace(
        hour=hour, minute=0, second=0, microsecond=0,
    )
    return d.strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture(autouse=True)
def _reset_market_news_refresh_rate_limit():
    # The /api/news/feed/?refresh=1 endpoint uses a class-level dict keyed by
    # user.id to throttle to 1/60s. Pytest-django rolls back the DB but recycles
    # user ids, so the dict leaks 429s across tests under CI parallel order.
    # Clear it before each test (mirrors the screener + manual-book fixtures).
    from apps.data.views import MarketNewsFeedView

    MarketNewsFeedView._last_refresh_at.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_offset(seconds: int) -> dt.datetime:
    return timezone.now() - dt.timedelta(seconds=seconds)


def _make_row(
    *,
    provider: str = "fmp",
    headline: str = "Apple beats expectations",
    url: str | None = None,
    source: str = "Reuters",
    symbols: list[str] | None = None,
    published_at: dt.datetime | None = None,
) -> MarketNewsItem:
    pub = published_at or timezone.now()
    return MarketNewsItem.objects.create(
        provider=provider,
        headline=headline,
        summary="A short summary.",
        url=url or f"https://{provider}.example/{abs(hash(headline)) % 10_000_000}",
        image_url="",
        source=source,
        published_at=pub,
        symbols=symbols or [],
        tags=[],
        dedup_key=dedup_key(headline, pub),
    )


# ---------------------------------------------------------------------------
# Dedup helper — behaviour-preserving refactor regression guard.
# ---------------------------------------------------------------------------


def test_dedup_key_helper_matches_previous_behaviour() -> None:
    """The extracted helper must produce the same hash that ``news.py`` did."""
    when = dt.datetime(2026, 5, 23, 9, 0, 0, tzinfo=dt.UTC)
    a = dedup_key("Apple ships new product today", when)
    b = dedup_key("Apple ships New Product Today", when)
    assert a == b  # case-insensitive normalisation
    assert len(a) == 32
    assert a != dedup_key("Apple ships new product tomorrow", when)


def test_dedup_key_used_by_news_service() -> None:
    """``news.py`` re-exports the helper as ``_dedup_key`` for source compat."""
    from apps.data.providers import news as news_module

    when = dt.datetime(2026, 5, 23, 9, 0, 0, tzinfo=dt.UTC)
    assert news_module._dedup_key("Hello world", when) == dedup_key(
        "Hello world", when
    )


# ---------------------------------------------------------------------------
# Provider adapter unit tests — using mocked httpx clients.
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class _FakeHttp:
    """Tracks every GET call so tests can assert URL + params."""

    def __init__(self, payloads: dict[str, list[dict]]):
        self.payloads = payloads
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None):
        self.calls.append((url, params or {}))
        # Resolve which payload applies by URL substring.
        for key, payload in self.payloads.items():
            if key in url:
                return _FakeResp(payload)
        return _FakeResp([])


def test_market_news_fmp_provider_maps_both_feeds() -> None:
    payloads = {
        "general-latest": [
            {
                "title": "Fed signals rate path",
                "text": "snippet",
                "publishedDate": _recent_iso(1, 9),
                "image": "https://img.example/g.png",
                "site": "Reuters",
                "url": "https://reuters.example/fed",
            },
            {
                # Skipped: missing url.
                "title": "noop",
                "publishedDate": _recent_iso(1, 9),
            },
        ],
        "stock-latest": [
            {
                "title": "Apple beats",
                "text": "earnings snippet",
                "publishedDate": _recent_space(1, 8),  # space format
                "image": "",
                "publisher": "CNBC",
                "url": "https://cnbc.example/aapl",
                "symbol": "AAPL",
            },
        ],
    }
    http = _FakeHttp(payloads)
    provider = MarketNewsFmpProvider(api_key="x", http=http)
    rows = provider.fetch_latest(limit=10)
    assert [r.headline for r in rows] == ["Fed signals rate path", "Apple beats"]
    assert rows[0].symbols == []
    assert rows[1].symbols == ["AAPL"]
    # Two endpoint calls (general + stock).
    assert sum(1 for url, _ in http.calls if "general-latest" in url) == 1
    assert sum(1 for url, _ in http.calls if "stock-latest" in url) == 1


def test_market_news_tiingo_provider_no_ticker_filter() -> None:
    payloads = {
        "tiingo/news": [
            {
                "title": "Macro headline",
                "description": "blurb",
                "url": "https://tiingo.example/macro",
                "publishedDate": _recent_iso(1, 9),
                "source": "Tiingo",
                "tags": ["fed", "rates"],
                "tickers": ["spy"],
            },
            {
                # crawlDate fallback.
                "title": "Older item",
                "description": "",
                "url": "https://tiingo.example/old",
                "publishedDate": "",
                "crawlDate": _recent_iso(2, 22),
                "source": "Tiingo",
            },
        ],
    }
    http = _FakeHttp(payloads)
    provider = MarketNewsTiingoProvider(api_key="x", http=http)
    rows = provider.fetch_latest(limit=10)
    assert {r.headline for r in rows} == {"Macro headline", "Older item"}
    # The adapter must not pass a tickers param.
    _url, params = http.calls[0]
    assert "tickers" not in params


# ---------------------------------------------------------------------------
# MarketNewsService aggregator.
# ---------------------------------------------------------------------------


class _StaticProvider:
    """Lightweight fake mimicking the adapters' protocol."""

    def __init__(self, name: str, rows: list[MarketNewsItem], raises: bool = False):
        self.name = name
        self._rows = rows
        self._raises = raises
        self.calls = 0

    def fetch_latest(self, *, limit: int = 60) -> list[MarketNewsItem]:
        self.calls += 1
        if self._raises:
            raise RuntimeError("provider boom")
        return list(self._rows)


def _unsaved(provider: str, url: str, headline: str, published_at: dt.datetime) -> MarketNewsItem:
    return MarketNewsItem(
        provider=provider,
        headline=headline,
        summary="",
        url=url,
        image_url="",
        source="Reuters",
        published_at=published_at,
        symbols=[],
        tags=[],
    )


@pytest.mark.django_db
def test_market_news_service_merges_persists_dedupes() -> None:
    now = timezone.now()
    fmp_rows = [
        _unsaved("fmp", "https://fmp.example/a", "Fed signals rate path", now),
    ]
    tiingo_rows = [
        # Same physical story — same headline, same day → same dedup_key.
        _unsaved("tiingo", "https://tiingo.example/a", "Fed signals rate path", now),
    ]
    svc = MarketNewsService(
        fmp=_StaticProvider("fmp", fmp_rows),
        tiingo=_StaticProvider("tiingo", tiingo_rows),
    )
    out = svc.fetch_latest()
    assert MarketNewsItem.objects.count() == 2
    keys = {r.dedup_key for r in MarketNewsItem.objects.all()}
    assert len(keys) == 1, "cross-provider rows must share a dedup_key"
    assert len(out) == 2


@pytest.mark.django_db
def test_market_news_service_freshness_short_circuit() -> None:
    """A row fetched < 15 min ago short-circuits the provider HTTP."""
    now = timezone.now()
    MarketNewsItem.objects.create(
        provider="fmp",
        headline="Fresh story",
        summary="",
        url="https://fmp.example/fresh",
        published_at=now,
    )
    p = _StaticProvider("fmp", [])
    svc = MarketNewsService(fmp=p)
    svc.fetch_latest()
    assert p.calls == 0, "freshness short-circuit must skip the provider"


@pytest.mark.django_db
def test_market_news_service_force_bypasses_freshness() -> None:
    now = timezone.now()
    MarketNewsItem.objects.create(
        provider="fmp",
        headline="Fresh story",
        summary="",
        url="https://fmp.example/fresh",
        published_at=now,
    )
    p = _StaticProvider("fmp", [])
    svc = MarketNewsService(fmp=p)
    svc.fetch_latest(force=True)
    assert p.calls == 1


@pytest.mark.django_db
def test_market_news_service_records_warnings_on_provider_failure() -> None:
    p1 = _StaticProvider("fmp", [], raises=True)
    p2 = _StaticProvider(
        "tiingo",
        [_unsaved("tiingo", "https://tiingo.example/a", "Story", timezone.now())],
    )
    svc = MarketNewsService(fmp=p1, tiingo=p2)
    out = svc.fetch_latest()
    assert any("fmp" in w for w in svc.warnings)
    assert len(out) == 1


@pytest.mark.django_db
def test_market_news_service_retention_prunes_old() -> None:
    old = timezone.now() - dt.timedelta(days=RETENTION_DAYS + 2)
    MarketNewsItem.objects.create(
        provider="fmp",
        headline="Old story",
        summary="",
        url="https://fmp.example/old",
        published_at=old,
        fetched_at=old,  # simulate old fetch
    )
    new_row = _unsaved("fmp", "https://fmp.example/new", "New story", timezone.now())
    svc = MarketNewsService(fmp=_StaticProvider("fmp", [new_row]))
    svc.fetch_latest(force=True)
    assert not MarketNewsItem.objects.filter(url="https://fmp.example/old").exists()


# ---------------------------------------------------------------------------
# Interest-ranking — pure-function unit tests.
# ---------------------------------------------------------------------------


def test_rank_feed_clusters_by_dedup_key() -> None:
    now = dt.datetime(2026, 5, 23, 12, 0, 0, tzinfo=dt.UTC)
    a = MarketNewsItem(
        provider="fmp",
        headline="A",
        url="u1",
        published_at=now - dt.timedelta(hours=1),
        dedup_key="K",
        source="Reuters",
        symbols=["AAPL"],
    )
    b = MarketNewsItem(
        provider="tiingo",
        headline="A",
        url="u2",
        published_at=now - dt.timedelta(hours=3),
        dedup_key="K",
        source="Tiingo",
        symbols=["AAPL"],
    )
    c = MarketNewsItem(
        provider="fmp",
        headline="B",
        url="u3",
        published_at=now - dt.timedelta(hours=2),
        dedup_key="L",
        source="unknown blog",
        symbols=[],
    )
    clusters = rank_feed([a, b, c], now=now)
    assert len(clusters) == 2
    # Cluster K has 2 members and is favoured by recency + breadth.
    top = clusters[0]
    assert top.cluster_size == 2
    assert top.representative.url == "u1"  # newest member


def test_rank_feed_recency_outweighs_breadth_for_close_recent() -> None:
    """Very-fresh single-source story should still rank well."""
    now = dt.datetime(2026, 5, 23, 12, 0, 0, tzinfo=dt.UTC)
    fresh = MarketNewsItem(
        headline="Fresh", url="u", published_at=now,
        dedup_key="F", source="Reuters", symbols=["AAPL"], provider="fmp",
    )
    older = MarketNewsItem(
        headline="Old", url="u2", published_at=now - dt.timedelta(hours=72),
        dedup_key="O", source="Reuters", symbols=["AAPL"], provider="fmp",
    )
    out = rank_feed([fresh, older], now=now)
    assert out[0].representative.url == "u"


def test_rank_feed_breadth_cap_constant() -> None:
    """``BREADTH_CAP`` constant exposed for unit-tested tuning."""
    assert BREADTH_CAP > 0


def test_source_weight_map_has_known_wires() -> None:
    for name in ("reuters", "bloomberg", "wsj"):
        assert SOURCE_WEIGHTS[name] == 1.0


# ---------------------------------------------------------------------------
# Sentiment classifier
# ---------------------------------------------------------------------------


@pytest.fixture
def sentiment_models(db):
    """Seed a minimal Llama + Qwen + DeepSeek + Haiku catalog."""
    from apps.models_catalog.models import ModelEntry

    ModelEntry.objects.all().delete()
    ModelEntry.objects.create(
        id="openrouter:qwen/qwen3.6-27b", provider="openrouter",
        display_name="Qwen3 27B", tier="hosted_open",
        is_active=True, price_in_per_mtok=Decimal("0.15"),
    )
    ModelEntry.objects.create(
        id="openrouter:meta-llama/llama-3.3-70b-instruct", provider="openrouter",
        display_name="Llama 3.3 70B", tier="hosted_open",
        is_active=True, price_in_per_mtok=Decimal("0.40"),
    )
    ModelEntry.objects.create(
        id="openrouter:deepseek/deepseek-r1", provider="openrouter",
        display_name="DeepSeek R1", tier="hosted_open",
        is_active=True, price_in_per_mtok=Decimal("0.55"),
    )
    ModelEntry.objects.create(
        id="anthropic:claude-haiku-4-5-20251001", provider="anthropic",
        display_name="Claude Haiku", tier="fast_cheap",
        is_active=True, price_in_per_mtok=Decimal("1.00"),
    )


def test_frugal_sentiment_models_filters_to_llama_qwen(sentiment_models) -> None:
    ids = [m.id for m in frugal_sentiment_models()]
    assert "openrouter:qwen/qwen3.6-27b" in ids
    assert "openrouter:meta-llama/llama-3.3-70b-instruct" in ids
    assert "openrouter:deepseek/deepseek-r1" not in ids
    assert "anthropic:claude-haiku-4-5-20251001" not in ids


def test_is_allowed_sentiment_model(sentiment_models) -> None:
    assert is_allowed_sentiment_model("openrouter:qwen/qwen3.6-27b")
    assert not is_allowed_sentiment_model("openrouter:deepseek/deepseek-r1")
    assert not is_allowed_sentiment_model("anthropic:claude-haiku-4-5-20251001")


@pytest.mark.django_db
def test_classify_one_batched_call(sentiment_models) -> None:
    rows = [_make_row(headline=f"News {i}") for i in range(3)]
    parsed = MarketNewsSentimentBatch(
        items=[
            NewsSentimentItem(idx=0, label="bullish", score=0.7, rationale="upgrade"),
            NewsSentimentItem(idx=1, label="bearish", score=-0.6, rationale="warning"),
            NewsSentimentItem(idx=2, label="neutral", score=0.0, rationale="factual"),
        ]
    )
    fake_resp = mock.Mock(
        text="{}", provider="openrouter", model="qwen/qwen3.6-27b",
        prompt_tokens=10, completion_tokens=10, cached_tokens=0,
        cost_usd=0.001, latency_ms=100,
    )
    with mock.patch(
        "hedgefund_agents.llm.structured.call_structured",
        return_value=(parsed, fake_resp),
    ) as mocked, mock.patch(
        "hedgefund_agents.registry.get_llm", return_value=mock.Mock()
    ), mock.patch(
        "hedgefund_agents._persist.record_llm_call", return_value=None
    ):
        ok, warn = classify(
            rows, model_id="openrouter:qwen/qwen3.6-27b", user_id=None
        )
    assert ok and warn is None
    assert mocked.call_count == 1, "one batched call, not one per row"
    saved = list(MarketNewsItem.objects.order_by("id"))
    assert [r.sentiment for r in saved] == ["bullish", "bearish", "neutral"]
    assert all(r.sentiment_model == "openrouter:qwen/qwen3.6-27b" for r in saved)


@pytest.mark.django_db
def test_classify_skips_rows_already_scored_by_active_model(sentiment_models) -> None:
    row = _make_row(headline="Already scored")
    row.sentiment = "bullish"
    row.sentiment_model = "openrouter:qwen/qwen3.6-27b"
    row.sentiment_at = timezone.now()
    row.save()

    with mock.patch(
        "hedgefund_agents.llm.structured.call_structured"
    ) as mocked, mock.patch(
        "hedgefund_agents.registry.get_llm", return_value=mock.Mock()
    ), mock.patch(
        "hedgefund_agents._persist.record_llm_call", return_value=None
    ):
        ok, warn = classify(
            [row], model_id="openrouter:qwen/qwen3.6-27b", user_id=None
        )
    assert ok and warn is None
    assert mocked.call_count == 0


@pytest.mark.django_db
def test_classify_missing_key_returns_actionable_warning(sentiment_models) -> None:
    row = _make_row(headline="Hello")
    with mock.patch(
        "hedgefund_agents.registry.get_llm",
        side_effect=RuntimeError(
            "No OPENROUTER key configured. Set your OPENROUTER key at /settings/models."
        ),
    ):
        ok, warn = classify(
            [row], model_id="openrouter:qwen/qwen3.6-27b", user_id=None
        )
    assert not ok
    assert warn is not None
    assert "OPENROUTER" in warn.upper()
    assert "/settings/models" in warn


# ---------------------------------------------------------------------------
# API endpoints — using the Django test client.
# ---------------------------------------------------------------------------


@pytest.fixture
def user(db):
    u = User.objects.create_user(
        email="market-news@test.local",
        password="testpass",
    )
    return u


@pytest.fixture
def auth_client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_news_preferences_auto_create(auth_client, user, sentiment_models) -> None:
    resp = auth_client.get("/api/news/preferences/")
    assert resp.status_code == 200
    body = resp.json()
    assert "preferences" in body
    assert body["preferences"]["sentiment_enabled"] is True
    assert body["preferences"]["sentiment_model"] == "openrouter:qwen/qwen3.6-27b"
    assert any(
        c["id"] == "openrouter:qwen/qwen3.6-27b"
        for c in body["sentiment_model_choices"]
    )


@pytest.mark.django_db
def test_news_preferences_rejects_haiku(auth_client, user, sentiment_models) -> None:
    resp = auth_client.put(
        "/api/news/preferences/",
        {"sentiment_model": "anthropic:claude-haiku-4-5-20251001"},
        format="json",
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_news_preferences_clamps_counts(auth_client, user, sentiment_models) -> None:
    resp = auth_client.put(
        "/api/news/preferences/",
        {"chyron_item_count": 99, "feed_item_count": 5},
        format="json",
    )
    assert resp.status_code == 200
    prefs = resp.json()["preferences"]
    assert prefs["chyron_item_count"] == 10
    # feed_item_count is legacy/unused (the feed paginates); clamped to [12, 48].
    assert prefs["feed_item_count"] == 12


@pytest.mark.django_db
def test_market_news_feed_returns_empty_with_no_keys(
    auth_client, user, sentiment_models
) -> None:
    # No providers, no persisted rows → empty + needs_keys=True.
    with mock.patch(
        "apps.data.providers.factory.get_market_news_service",
        return_value=MarketNewsService(),
    ):
        resp = auth_client.get("/api/news/feed/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["needs_keys"] is True
    assert body["sentiment_enabled"] is True


@pytest.mark.django_db
def test_market_news_feed_serves_persisted_rows(
    auth_client, user, sentiment_models, settings
) -> None:
    """With sentiment off and rows already persisted, the endpoint returns
    them without making any provider HTTP or LLM calls."""
    UserNewsPreferences.objects.create(user=user, sentiment_enabled=False)
    for i in range(3):
        _make_row(
            headline=f"Story {i}",
            url=f"https://fmp.example/{i}",
            published_at=timezone.now() - dt.timedelta(hours=i),
        )
    with mock.patch(
        "apps.data.providers.factory.get_market_news_service"
    ) as f:
        svc = MarketNewsService()
        f.return_value = svc
        resp = auth_client.get("/api/news/feed/")
    body = resp.json()
    assert resp.status_code == 200
    assert len(body["items"]) == 3
    assert body["sentiment_enabled"] is False


@pytest.mark.django_db
def test_market_news_feed_refresh_rate_limited(
    auth_client, user, sentiment_models
) -> None:
    with mock.patch(
        "apps.data.providers.factory.get_market_news_service"
    ) as f:
        f.return_value = MarketNewsService()
        r1 = auth_client.get("/api/news/feed/?refresh=1")
        assert r1.status_code == 200
        r2 = auth_client.get("/api/news/feed/?refresh=1")
        assert r2.status_code == 429


@pytest.mark.django_db
def test_market_news_feed_pagination(
    auth_client, user, sentiment_models
) -> None:
    """Feed returns 12 per page, advertises has_more, and caps at 48."""
    UserNewsPreferences.objects.create(user=user, sentiment_enabled=False)
    # Seed 20 persisted rows so we have at least 2 pages of clusters.
    for i in range(20):
        _make_row(
            headline=f"Story {i}",
            url=f"https://fmp.example/{i}",
            published_at=timezone.now() - dt.timedelta(hours=i),
        )
    with mock.patch(
        "apps.data.providers.factory.get_market_news_service"
    ) as f:
        f.return_value = MarketNewsService()
        r1 = auth_client.get("/api/news/feed/")
        body1 = r1.json()
        r2 = auth_client.get("/api/news/feed/?page=2")
        body2 = r2.json()

    assert r1.status_code == 200 and r2.status_code == 200
    assert body1["page"] == 1
    assert body1["page_size"] == 12
    assert len(body1["items"]) == 12
    assert body1["has_more"] is True
    assert body1["total_available"] == 20

    assert body2["page"] == 2
    assert len(body2["items"]) == 8
    assert body2["has_more"] is False
    # Pages must not overlap: every id on page 2 should differ from page 1.
    ids1 = {item["id"] for item in body1["items"]}
    ids2 = {item["id"] for item in body2["items"]}
    assert ids1.isdisjoint(ids2)


# ---------------------------------------------------------------------------
# PIT architecture guard.
# ---------------------------------------------------------------------------


BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent

FORBIDDEN_IMPORT_PATTERNS = [
    re.compile(r"^\s*(?:from|import)\s+apps\.data\.providers\.market_news\b", re.MULTILINE),
    re.compile(r"^\s*(?:from|import)\s+apps\.data\.market_news_rank\b", re.MULTILINE),
    re.compile(r"^\s*(?:from|import)\s+apps\.data\.market_news_sentiment\b", re.MULTILINE),
]


def _iter_pit_paths():
    for p in (BACKEND_ROOT / "apps" / "backtests").rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p
    for p in (BACKEND_ROOT / "hedgefund_agents").rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p


def test_market_news_not_imported_from_pit_paths() -> None:
    offenders: list[tuple[str, str]] = []
    for path in _iter_pit_paths():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for pat in FORBIDDEN_IMPORT_PATTERNS:
            if pat.search(text):
                offenders.append(
                    (str(path.relative_to(BACKEND_ROOT)), pat.pattern)
                )
    assert not offenders, (
        "The market-news stack must not be imported from any backtest / "
        f"point-in-time path. Offenders: {offenders}"
    )


def test_market_news_item_has_no_as_of_field() -> None:
    """A backtest must never be able to key on an ``as_of_date`` field on
    ``MarketNewsItem``; that field exists only for PIT models like
    ``NewsItem`` (which is fine — NewsItem is the PIT model).
    """
    field_names = {f.name for f in MarketNewsItem._meta.get_fields()}
    forbidden = {"as_of", "as_of_date", "vintage_date", "effective_date"}
    assert not (field_names & forbidden), (
        "MarketNewsItem must carry only live ('today') timestamps. "
        f"Found PIT-style fields: {field_names & forbidden}"
    )
