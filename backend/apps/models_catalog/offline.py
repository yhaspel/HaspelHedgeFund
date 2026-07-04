"""Local-model resolution for offline mode (P4-OFF R3).

Offline runs use the local Ollama model *only*. This module resolves which
local tag to use (``OFFLINE_LLM_MODEL`` if discovered, else the first discovered
model, else a hard error — never a cloud fallback) and builds the forced
per-agent override map applied at every execution seam.
"""
from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

from .ollama_discovery import discover_ollama_models
from .presets import ALL_AGENTS

log = logging.getLogger(__name__)

DEFAULT_OFFLINE_MODEL = "qwen2.5:7b"


class LocalModelUnavailable(RuntimeError):
    """Raised when OFFLINE_MODE needs a local model but Ollama has none.

    Actionable by design — the message tells the operator exactly how to fix it.
    Never degrades to a cloud model (that would defeat the offline guarantee).
    """


def offline_host(user: Any = None) -> str:
    """The Ollama host to probe: the user's saved ``ProviderKey.ollama_host``
    when one is in scope, else ``settings.OLLAMA_HOST`` (default localhost).

    The health probe and preset seams pass ``user=None`` (unauthenticated /
    process default); run/cycle seams pass the run's user so a per-user host
    still wins, exactly as it does online.
    """
    if user is not None:
        try:
            from .models import ProviderKey

            uid = int(getattr(user, "id", user))
            pk = ProviderKey.objects.filter(user_id=uid).first()
            if pk is not None and pk.ollama_host:
                return pk.ollama_host
        except Exception:  # noqa: BLE001 — best-effort; fall through to env host
            pass
    return getattr(settings, "OLLAMA_HOST", "") or "http://localhost:11434"


def resolve_local_model(user: Any = None, *, host: str = "") -> str:
    """Return the ``ollama:<tag>`` id to force every agent onto, or raise.

    Order: ``OFFLINE_LLM_MODEL`` (default ``qwen2.5:7b``) when that tag is
    discovered on the host, else the first discovered model, else
    :class:`LocalModelUnavailable`.
    """
    host = host or offline_host(user)
    discovered = discover_ollama_models(host)
    if not discovered:
        raise LocalModelUnavailable(
            f"OFFLINE_MODE is on but no local Ollama model was found at {host}. "
            "Start Ollama and pull a model, e.g. "
            "`brew services start ollama && ollama pull qwen2.5:7b`."
        )
    want = getattr(settings, "OFFLINE_LLM_MODEL", "") or DEFAULT_OFFLINE_MODEL
    want_id = f"ollama:{want}"
    ids = [m["id"] for m in discovered]
    chosen = want_id if want_id in ids else ids[0]
    log.info("offline_llm resolved=%s want=%s discovered=%d", chosen, want_id, len(ids))
    return chosen


def offline_model_overrides(user: Any = None, *, host: str = "") -> dict[str, str]:
    """Every agent → the resolved local model. This is the expanded ``local``
    preset; applied at each execution seam so no agent can resolve a cloud slug.
    """
    model_id = resolve_local_model(user, host=host)
    return {agent: model_id for agent in ALL_AGENTS}
