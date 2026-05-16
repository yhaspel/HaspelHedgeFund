"""Two-tier caching for provider responses.

L1: in-process / Redis for raw HTTP response bodies (short TTL).
L2: Postgres (DailyBar / Fundamental / FilingRecord) for canonical, queryable
    rows. The agent layer reads exclusively from L2.

For P1 we only implement L2 + a thin Redis JSON cache for the HTTP layer.
"""
from __future__ import annotations

import json
from typing import Any

import redis
from django.conf import settings

_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


def cache_get(key: str) -> Any | None:
    try:
        raw = _redis().get(key)
    except redis.RedisError:
        return None
    return json.loads(raw) if raw else None


def cache_set(key: str, value: Any, ttl_seconds: int = 86_400) -> None:
    try:
        _redis().setex(key, ttl_seconds, json.dumps(value, default=str))
    except redis.RedisError:
        pass
