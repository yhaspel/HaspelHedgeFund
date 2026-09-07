"""Probe a user's Ollama host and surface its models.

Cached 60s per host. Failures are silent (host unreachable just means
local models don't appear in the dropdown).
"""
from __future__ import annotations

import logging
import time
from decimal import Decimal

import httpx

log = logging.getLogger(__name__)

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

    Returns [] if host is empty, unsafe (see ``is_safe_ollama_host``) or
    unreachable. This function runs on unauthenticated-ish read paths
    (``GET /api/models/``, ``/api/presets/<name>/``, the health probe), so it
    must never raise: a stored host that fails validation, is malformed, or is
    simply down all mean the same thing to the caller — no local models.
    """
    if not host:
        return []
    now = time.monotonic()
    cached = _CACHE.get(host)
    if cached and now - cached[0] < _TTL:
        return cached[1]
    # Defence in depth: rows saved before ollama_host was validated are still in
    # the database, and this is the function that turns one into a live request.
    from .serializers import is_safe_ollama_host

    if not is_safe_ollama_host(host):
        log.warning("ollama discovery refused an unsafe host (SSRF guard)")
        _CACHE[host] = (now, [])
        return []
    try:
        r = httpx.get(f"{host.rstrip('/')}/api/tags", timeout=2.0)
        r.raise_for_status()
        tags = r.json().get("models", [])
    except (httpx.HTTPError, httpx.InvalidURL, httpx.UnsupportedProtocol, ValueError):
        # httpx.InvalidURL does NOT inherit from httpx.HTTPError, so a malformed
        # stored host used to escape this handler and 500 every catalog read.
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
