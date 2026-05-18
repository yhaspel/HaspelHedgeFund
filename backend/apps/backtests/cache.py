"""L2 LLM response cache.

Keyed on sha256(agent_name || agent_version || model || messages || schema).
Backtests prime this once per agent_version + master window; the IS sweep
and OOS replay then re-read PM aggregation cheaply without re-calling LLMs.

The cache is invalidated automatically when:
  - AgentVersion bumps (changes prompt/config/model default).
  - Any input row hashed into the message body changes.
"""
from __future__ import annotations

import hashlib
import json

from django.db import transaction
from django.utils import timezone


def build_key(
    *, agent_name: str, agent_version: str, model: str,
    messages: list, schema_name: str = "",
) -> str:
    payload = {
        "agent": agent_name,
        "version": agent_version,
        "model": model,
        "schema": schema_name,
        "messages": [
            {"role": getattr(m, "role", None), "content": getattr(m, "content", str(m))}
            for m in messages
        ],
    }
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def lookup(cache_key: str) -> dict | None:
    """Returns the cached `response_json` dict or None."""
    from .models import LLMResponseCache  # local import: avoid load-time cycle

    try:
        row = LLMResponseCache.objects.get(cache_key=cache_key)
    except LLMResponseCache.DoesNotExist:
        return None
    LLMResponseCache.objects.filter(pk=row.pk).update(
        hits=row.hits + 1, last_hit_at=timezone.now()
    )
    return row.response_json


def store(
    *, cache_key: str, agent_name: str, agent_version: str,
    response_json: dict, tokens_in: int, tokens_out: int, cost_usd: float,
) -> None:
    from .models import LLMResponseCache

    with transaction.atomic():
        LLMResponseCache.objects.update_or_create(
            cache_key=cache_key,
            defaults={
                "agent_name": agent_name,
                "agent_version": agent_version,
                "response_json": response_json,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": cost_usd,
                "hits": 0,
            },
        )


def make_cache_ctx(state: dict, agent_name: str) -> dict | None:
    """Build a cache_ctx dict to pass into `call_structured`.
    Returns None if caching is disabled for this state."""
    if not state.get("use_llm_cache"):
        return None
    versions = state.get("agent_versions") or {}
    return {
        "agent_name": agent_name,
        "agent_version": str(versions.get(agent_name, "")),
        "enabled": True,
    }


# Telemetry counters used by tests to assert "zero LLM calls during sweep".
_counters: dict[str, int] = {"hits": 0, "misses": 0, "writes": 0}


def counter(key: str) -> int:
    return _counters.get(key, 0)


def reset_counters() -> None:
    for k in _counters:
        _counters[k] = 0


def _bump(key: str) -> None:
    _counters[key] = _counters.get(key, 0) + 1
