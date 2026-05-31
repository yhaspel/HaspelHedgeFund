"""Per-user data-provider factories (P2n BYOK).

Each factory resolves a key from `ProviderKey` for the given user, falling
back to the platform env var only when policy allows. Mirrors the LLM
resolver in `hedgefund_agents/registry.py::get_llm`.

Resolution order (see `_resolve_data_key`):
  1. User key (from `ProviderKey`)
  2. `force_platform=True` kwarg → env var
  3. Provider in `_PUBLIC` (FRED) → env var
  4. `settings.ALLOW_PLATFORM_DATA_KEYS` → env var
  5. Raise `RuntimeError` with actionable message pointing at `/settings/models`

The (user_id, api_key) tuple is the cache key so a freshly-saved BYO key
does not reuse a platform-key client. Stale entries die with the worker.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from django.conf import settings

from .edgar import EdgarProvider
from .fmp import FmpProvider
from .fred import FredProvider
from .market_news import MarketNewsService
from .market_news_fmp import MarketNewsFmpProvider
from .market_news_tiingo import MarketNewsTiingoProvider
from .news import NewsService
from .news_fmp import FmpNewsProvider
from .news_tiingo import TiingoNewsProvider
from .ownership import OwnershipResolver

log = logging.getLogger(__name__)

# Public-data providers: env-fallback always allowed (free, zero redistribution risk).
_PUBLIC = ("fred",)


def _uid(user: Any) -> int | None:
    if user is None:
        return None
    return int(getattr(user, "id", user))


def _resolve_data_key(
    user: Any, provider: str, env_key: str, *, force_platform: bool = False
) -> tuple[str, str]:
    """Return ``(api_key, source)`` where source ∈ {"user", "platform"}.

    Raises ``RuntimeError`` with an actionable message if no key is available
    and policy forbids env-fallback for this provider.
    """
    from apps.models_catalog.models import ProviderKey  # local: avoid app-load cycle

    uid = _uid(user)
    if uid is not None:
        pk = ProviderKey.objects.filter(user_id=uid).first()
        if pk is not None and pk.has_key(provider):
            return pk.get_key(provider), "user"

    if (
        force_platform
        or provider in _PUBLIC
        or getattr(settings, "ALLOW_PLATFORM_DATA_KEYS", False)
    ):
        return getattr(settings, env_key, ""), "platform"

    raise RuntimeError(
        f"No {provider.upper()} key configured for this user. "
        f"Set your {provider.upper()} key at /settings/models."
    )


# ---------------------------------------------------------------------------
# Cached constructors. The cache key includes the api_key so a key rotation
# yields a fresh client (the previous entry stays cached but is unreferenced
# from the user-facing factories below).
# ---------------------------------------------------------------------------


@lru_cache(maxsize=128)
def _cached_fmp(user_id: int | None, api_key: str) -> FmpProvider:
    return FmpProvider(api_key=api_key or None)


@lru_cache(maxsize=128)
def _cached_fmp_news(user_id: int | None, api_key: str) -> FmpNewsProvider:
    return FmpNewsProvider(api_key=api_key or None)


@lru_cache(maxsize=128)
def _cached_tiingo_news(user_id: int | None, api_key: str) -> TiingoNewsProvider:
    return TiingoNewsProvider(api_key=api_key or None)


@lru_cache(maxsize=128)
def _cached_market_news_fmp(user_id: int | None, api_key: str) -> MarketNewsFmpProvider:
    return MarketNewsFmpProvider(api_key=api_key or None)


@lru_cache(maxsize=128)
def _cached_market_news_tiingo(
    user_id: int | None, api_key: str
) -> MarketNewsTiingoProvider:
    return MarketNewsTiingoProvider(api_key=api_key or None)


@lru_cache(maxsize=128)
def _cached_fred(user_id: int | None, api_key: str) -> FredProvider:
    return FredProvider(api_key=api_key or None)


@lru_cache(maxsize=1)
def _cached_edgar() -> EdgarProvider:
    return EdgarProvider()


@lru_cache(maxsize=128)
def _cached_ownership(user_id: int | None, api_key: str) -> OwnershipResolver:
    fmp = FmpProvider(api_key=api_key or None) if api_key else None
    return OwnershipResolver(fmp=fmp, edgar=_cached_edgar())


# ---------------------------------------------------------------------------
# Public factory API.
# ---------------------------------------------------------------------------


def get_fmp_provider(user: Any = None, *, force_platform: bool = False) -> FmpProvider:
    key, source = _resolve_data_key(user, "fmp", "FMP_API_KEY", force_platform=force_platform)
    log.info("data_provider provider=fmp user_id=%s key_source=%s", _uid(user), source)
    return _cached_fmp(_uid(user) if source == "user" else None, key)


def get_fmp_news_provider(
    user: Any = None, *, force_platform: bool = False
) -> FmpNewsProvider:
    key, source = _resolve_data_key(user, "fmp", "FMP_API_KEY", force_platform=force_platform)
    log.info("data_provider provider=fmp_news user_id=%s key_source=%s", _uid(user), source)
    return _cached_fmp_news(_uid(user) if source == "user" else None, key)


def get_tiingo_news_provider(
    user: Any = None, *, force_platform: bool = False
) -> TiingoNewsProvider:
    key, source = _resolve_data_key(
        user, "tiingo", "TIINGO_API_KEY", force_platform=force_platform
    )
    log.info("data_provider provider=tiingo_news user_id=%s key_source=%s", _uid(user), source)
    return _cached_tiingo_news(_uid(user) if source == "user" else None, key)


def get_fred_provider(user: Any = None, *, force_platform: bool = False) -> FredProvider:
    key, source = _resolve_data_key(
        user, "fred", "FRED_API_KEY", force_platform=force_platform
    )
    log.info("data_provider provider=fred user_id=%s key_source=%s", _uid(user), source)
    return _cached_fred(_uid(user) if source == "user" else None, key)


def get_market_news_fmp_provider(
    user: Any = None, *, force_platform: bool = False
) -> MarketNewsFmpProvider:
    key, source = _resolve_data_key(
        user, "fmp", "FMP_API_KEY", force_platform=force_platform
    )
    log.info(
        "data_provider provider=market_news_fmp user_id=%s key_source=%s",
        _uid(user), source,
    )
    return _cached_market_news_fmp(_uid(user) if source == "user" else None, key)


def get_market_news_tiingo_provider(
    user: Any = None, *, force_platform: bool = False
) -> MarketNewsTiingoProvider:
    key, source = _resolve_data_key(
        user, "tiingo", "TIINGO_API_KEY", force_platform=force_platform
    )
    log.info(
        "data_provider provider=market_news_tiingo user_id=%s key_source=%s",
        _uid(user), source,
    )
    return _cached_market_news_tiingo(_uid(user) if source == "user" else None, key)


def get_market_news_service(
    user: Any = None, *, force_platform: bool = False
) -> MarketNewsService:
    """Build a ``MarketNewsService`` with whichever provider keys are available.

    Missing-key ``RuntimeError`` is swallowed so a user with only one key still
    gets a partial feed. With no keys at all, the service has no providers and
    ``fetch_latest`` will return whatever rows are already persisted.
    """
    fmp: MarketNewsFmpProvider | None = None
    tiingo: MarketNewsTiingoProvider | None = None
    try:
        fmp = get_market_news_fmp_provider(user, force_platform=force_platform)
    except RuntimeError:
        pass
    try:
        tiingo = get_market_news_tiingo_provider(user, force_platform=force_platform)
    except RuntimeError:
        pass
    return MarketNewsService(fmp=fmp, tiingo=tiingo)


def get_news_service(user: Any = None, *, force_platform: bool = False) -> NewsService:
    """Tries both Tiingo and FMP news factories. Missing-key RuntimeErrors are
    silently swallowed so a user with only one provider key still gets news.

    If both keys are missing in prod with no ``force_platform``, returns a
    NewsService with zero providers — ``fetch_and_persist`` yields the cached
    rows (if any) and never raises.
    """
    tiingo: TiingoNewsProvider | None = None
    fmp: FmpNewsProvider | None = None
    try:
        tiingo = get_tiingo_news_provider(user, force_platform=force_platform)
    except RuntimeError:
        pass
    try:
        fmp = get_fmp_news_provider(user, force_platform=force_platform)
    except RuntimeError:
        pass
    return NewsService(tiingo=tiingo, fmp=fmp)


def get_edgar_provider() -> EdgarProvider:
    """EDGAR has no per-user key (User-Agent only). Factory exists for API
    symmetry with the other providers and to centralize where EDGAR is built.
    """
    return _cached_edgar()


def get_ownership_provider(user: Any = None) -> OwnershipResolver:
    """Build the 13F ownership resolver (FMP-if-entitled-else-EDGAR).

    Resolves the FMP key like ``get_fmp_provider``; a missing key (the
    ``RuntimeError`` from ``_resolve_data_key``) yields ``fmp=None`` so the
    resolver runs EDGAR-only. EDGAR (User-Agent only) is always attached.
    ``lru_cache``d on ``(user_id, api_key)`` exactly like ``_cached_fmp``.
    """
    key = ""
    source = "platform"
    try:
        key, source = _resolve_data_key(user, "fmp", "FMP_API_KEY")
    except RuntimeError:
        key = ""
    log.info(
        "data_provider provider=ownership user_id=%s key_source=%s",
        _uid(user), source if key else "none",
    )
    return _cached_ownership(_uid(user) if (key and source == "user") else None, key)


def _reset_caches_for_tests() -> None:
    """Test helper: clear every lru_cache so a freshly-saved key takes effect."""
    _cached_fmp.cache_clear()
    _cached_fmp_news.cache_clear()
    _cached_tiingo_news.cache_clear()
    _cached_market_news_fmp.cache_clear()
    _cached_market_news_tiingo.cache_clear()
    _cached_fred.cache_clear()
    _cached_edgar.cache_clear()
    _cached_ownership.cache_clear()
