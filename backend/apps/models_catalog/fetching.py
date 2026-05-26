"""Sync dev/frugal ModelEntry rows from the live OpenRouter catalog.

Only metadata is fetched. Menu *membership* (which slugs are offered) is
the curated allowlist in tier_menus.py — a one-line code edit to add a
brand-new model. Pricing, context window, display name, capability flags,
and free-vs-paid status are all live.

The catalog endpoint is reused via the module reference
``verification.fetch_openrouter_catalog`` so existing tests that patch
``apps.models_catalog.verification.fetch_openrouter_catalog`` still
intercept the call here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

import httpx
from django.utils import timezone

from . import verification
from .models import ModelEntry
from .tier_menus import (
    DEV_TIER_SLUGS,
    FRUGAL_PRICE_CEILING_IN,
    FRUGAL_PRICE_CEILING_OUT,
    FRUGAL_TIER_SLUGS,
)


@dataclass
class SyncResult:
    synced: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    deactivated: list[str] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)
    fetched_at: datetime | None = None

    def as_dict(self) -> dict:
        return {
            "synced": list(self.synced),
            "created": list(self.created),
            "deactivated": list(self.deactivated),
            "excluded": list(self.excluded),
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
        }


def _derive_fields(slug: str, payload: dict) -> dict:
    pricing = payload.get("pricing") or {}
    p_in = verification._per_token_to_per_mtok(pricing.get("prompt"))
    p_out = verification._per_token_to_per_mtok(pricing.get("completion"))
    free = (p_in is None or p_in == 0) and (p_out is None or p_out == 0)
    name = payload.get("name") or slug
    suffix = " (OpenRouter Free)" if free else " (OpenRouter)"
    top_provider = payload.get("top_provider") or {}
    context_window = top_provider.get("context_length") or payload.get("context_length") or 0
    supported = payload.get("supported_parameters") or []
    return {
        "id": f"openrouter:{slug}",
        "provider": "openrouter",
        "display_name": f"{name}{suffix}",
        "tier": "hosted_open",
        "context_window": int(context_window or 0),
        "supports_caching": (
            "input_cache_read" in pricing or "input_cache_write" in pricing
        ),
        "supports_structured_output": (
            "structured_outputs" in supported or "response_format" in supported
        ),
        "supports_long_context": int(context_window or 0) >= 200_000,
        "price_in_per_mtok": p_in,
        "price_out_per_mtok": p_out,
        "is_active": True,
    }


def _guard_violation(slug: str, fields: dict) -> str | None:
    """Return a non-empty reason string when a slug fails its tier guard."""
    p_in = fields["price_in_per_mtok"] or Decimal("0")
    p_out = fields["price_out_per_mtok"] or Decimal("0")
    if slug in DEV_TIER_SLUGS:
        if p_in > 0 or p_out > 0:
            return "no longer free"
        return None
    # frugal
    if p_in > FRUGAL_PRICE_CEILING_IN or p_out > FRUGAL_PRICE_CEILING_OUT:
        return (
            f"exceeds frugal ceiling "
            f"(in={p_in} > {FRUGAL_PRICE_CEILING_IN} "
            f"or out={p_out} > {FRUGAL_PRICE_CEILING_OUT})"
        )
    return None


def sync_tier_models(
    *,
    http: httpx.Client | None = None,
    dry_run: bool = False,
) -> SyncResult:
    """Fetch the live OpenRouter catalog and refresh ModelEntry rows for the
    dev + frugal allowlist slugs. Pure metadata sync — never touches presets
    or static-tier rows. Aborts before any DB write if the fetch fails.
    """
    catalog = verification.fetch_openrouter_catalog(http=http)
    now = timezone.now()
    result = SyncResult(fetched_at=now)

    allowlist = list({*DEV_TIER_SLUGS, *FRUGAL_TIER_SLUGS})
    # Stable order for reporting.
    allowlist.sort()

    for slug in allowlist:
        mid = f"openrouter:{slug}"
        upstream = catalog.get(slug)
        existing = ModelEntry.objects.filter(id=mid).first()

        if upstream is None:
            # Missing upstream — deactivate (never hard-delete).
            if existing and existing.is_active:
                if not dry_run:
                    existing.is_active = False
                    existing.last_verified_at = now
                    existing.last_verified_note = (
                        f"slug {slug!r} not in OpenRouter /api/v1/models response"
                    )
                    existing.save(update_fields=[
                        "is_active", "last_verified_at", "last_verified_note",
                    ])
                result.deactivated.append(mid)
            continue

        fields = _derive_fields(slug, upstream)
        violation = _guard_violation(slug, fields)
        if violation:
            result.excluded.append({"slug": slug, "reason": violation})
            if existing and existing.is_active:
                if not dry_run:
                    existing.is_active = False
                    existing.last_verified_at = now
                    existing.last_verified_note = f"excluded: {violation}"
                    existing.save(update_fields=[
                        "is_active", "last_verified_at", "last_verified_note",
                    ])
            continue

        # Healthy — upsert. Don't clobber `notes` (user-editable).
        defaults = {k: v for k, v in fields.items() if k != "id"}
        defaults["last_verified_at"] = now
        defaults["last_verified_note"] = ""
        if dry_run:
            result.synced.append(mid)
            if existing is None:
                result.created.append(mid)
            continue
        _, created = ModelEntry.objects.update_or_create(id=mid, defaults=defaults)
        result.synced.append(mid)
        if created:
            result.created.append(mid)

    return result
