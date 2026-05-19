"""Probe a user's Ollama host and surface its models.

Cached 60s per host. Failures are silent (host unreachable just means
local models don't appear in the dropdown).
"""
from __future__ import annotations

import time
from decimal import Decimal

import httpx

_CACHE: dict[str, tuple[float, list[dict]]] = {}
_TTL = 60.0

# Coarse hardware-tier inference. Local models contribute $0 to cost.
TIER_HINTS = {
    "qwen": "local-A",
    "llama3.3": "local-B",
    "llama3.2": "local-A",
    "deepseek": "local-C",
    "mixtral": "local-B",
}


def _infer_tier(name: str) -> str:
    n = name.lower()
    for needle, tier in TIER_HINTS.items():
        if needle in n:
            return tier
    return "local-A"


def discover_ollama_models(host: str) -> list[dict]:
    """Return list of dicts shaped like ModelEntry.

    Returns [] if host is empty or unreachable.
    """
    if not host:
        return []
    now = time.monotonic()
    cached = _CACHE.get(host)
    if cached and now - cached[0] < _TTL:
        return cached[1]
    try:
        r = httpx.get(f"{host.rstrip('/')}/api/tags", timeout=2.0)
        r.raise_for_status()
        tags = r.json().get("models", [])
    except (httpx.HTTPError, ValueError):
        _CACHE[host] = (now, [])
        return []
    out = []
    for t in tags:
        name = t.get("name") or t.get("model")
        if not name:
            continue
        out.append({
            "id": f"ollama:{name}",
            "provider": "ollama",
            "display_name": f"{name} (local)",
            "tier": "local",
            "context_window": 32_768,
            "supports_caching": False,
            "supports_structured_output": True,
            "supports_long_context": False,
            "price_in_per_mtok": Decimal("0"),
            "price_out_per_mtok": Decimal("0"),
            "is_active": True,
            "notes": _infer_tier(name),
        })
    _CACHE[host] = (now, out)
    return out
