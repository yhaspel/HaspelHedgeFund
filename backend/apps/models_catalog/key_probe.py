"""Provider-key probe: find a dead API key before the scheduled work that needs it.

A rejected API key otherwise surfaces only as the failures of the work that
needs it — council runs failing within seconds, a pre-flight bar refresh
failing into a "stale data" skip. The beat task
(``tasks.probe_provider_keys``) makes ONE cheap authenticated call per DISTINCT
key in use — the platform env key plus every user's BYO key — and alerts the
operator once per outage per key.

Keys are decrypted in-process for the request only and are identified everywhere
else (logs, alerts, the task result) by a short sha256 fingerprint. Only 401/403
count as a rejected key: a timeout, network error, 429 or 5xx says nothing about
the key and changes nothing. A key that answers 2xx again closes its outage, so
a later rejection alerts again.
"""

from __future__ import annotations

import hashlib
import logging

import httpx
from django.conf import settings
from django.core.cache import cache

log = logging.getLogger(__name__)

OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
FMP_PROBE_SYMBOL = "SPY"
REJECTED_STATUSES = frozenset({401, 403})
PROBE_TIMEOUT_S = 10.0
# Open-outage marker per (provider, key fingerprint). No expiry: the outage
# stays open (no repeat alert) until the key answers again.
_OUTAGE_KEY = "key_probe:rejected:{provider}:{fingerprint}"


def fingerprint(key: str) -> str:
    """Short, non-reversible id for a key — safe to log and to alert on."""
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _keys_in_use(provider: str, platform_key: str) -> dict[str, tuple[str, list[str]]]:
    """fingerprint → (key, who uses it) for the platform key plus every stored
    BYO key of ``provider``. A stored value that will not decrypt reads as ""
    and is skipped (decrypt() logs it)."""
    from .models import ProviderKey

    keys: dict[str, tuple[str, list[str]]] = {}

    def add(key: str, who: str) -> None:
        if key:
            keys.setdefault(fingerprint(key), (key, []))[1].append(who)

    add(platform_key, "platform")
    stored = ProviderKey.objects.exclude(**{f"{provider}_api_key_enc": ""}).order_by("user_id")
    for pk in stored:
        add(pk.get_key(provider), f"user #{pk.user_id}")
    return keys


def _status(http: httpx.Client, provider: str, key: str) -> int | None:
    """HTTP status of one cheap authenticated call, or None when the call
    itself failed (timeout, network) — which proves nothing about the key."""
    try:
        if provider == "openrouter":
            resp = http.get(OPENROUTER_KEY_URL, headers={"Authorization": f"Bearer {key}"})
        else:
            from apps.data.providers.fmp import BASE_URL

            resp = http.get(
                f"{BASE_URL}/quote-short", params={"symbol": FMP_PROBE_SYMBOL, "apikey": key}
            )
    except Exception as exc:  # noqa: BLE001 — class only: an httpx message quotes the URL
        log.warning("key probe: %s call failed (%s)", provider, type(exc).__name__)
        return None
    return resp.status_code


def _record(provider: str, label: str, fp: str, status: int | None, used_by: list[str]) -> str:
    """Fold one probe result into the outage state; alert on a NEW rejection."""
    outage = _OUTAGE_KEY.format(provider=provider, fingerprint=fp)
    if status is not None and 200 <= status < 300:
        cache.delete(outage)  # answered: any open outage is over
        return "ok"
    if status not in REJECTED_STATUSES:
        return "inconclusive"
    log.error(
        "key probe: %s rejected key fp=%s (HTTP %s; used by %s)",
        provider, fp, status, ", ".join(used_by),
    )
    # cache.add is atomic: exactly one probe opens the outage and alerts.
    if cache.add(outage, status, None):
        from apps.notifications.operator import notify_provider_key_rejected

        if not notify_provider_key_rejected(label, fp, status_code=status, used_by=used_by):
            cache.delete(outage)  # not delivered — let the next probe try again
    return "rejected"


def probe_all() -> dict[str, dict[str, str]]:
    """Probe every distinct OpenRouter and FMP key in use. Returns
    ``{provider: {fingerprint: "ok" | "rejected" | "inconclusive" | "error"}}``."""
    targets = (
        ("openrouter", "OpenRouter", getattr(settings, "OPENROUTER_API_KEY", "")),
        ("fmp", "FMP", getattr(settings, "FMP_API_KEY", "")),
    )
    out: dict[str, dict[str, str]] = {}
    with httpx.Client(timeout=PROBE_TIMEOUT_S) as http:
        for provider, label, platform_key in targets:
            results: dict[str, str] = {}
            for fp, (key, used_by) in _keys_in_use(provider, platform_key).items():
                try:
                    status = _status(http, provider, key)
                    results[fp] = _record(provider, label, fp, status, used_by)
                except Exception:  # noqa: BLE001 — one key's bookkeeping never stops the rest
                    log.exception("key probe: %s fp=%s could not be recorded", provider, fp)
                    results[fp] = "error"
            out[provider] = results
    return out
