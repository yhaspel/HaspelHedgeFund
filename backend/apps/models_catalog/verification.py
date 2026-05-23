"""OpenRouter pricing verification shared between the management command and
the verify-pricing API endpoint.

Pricing semantics:
  - OpenRouter exposes pricing in USD-per-token at GET /api/v1/models.
  - We store USD-per-million-tokens on ModelEntry.
  - A model is "free" when both prompt and completion pricing are exactly 0.
  - The :free suffix is convention; the authoritative signal is pricing==0.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

import httpx
from django.utils import timezone

from .models import ModelEntry

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
PRICE_TOLERANCE = Decimal("0.0001")  # USD/Mtok — guards against float noise


@dataclass
class VerificationResult:
    model_id: str
    ok: bool
    note: str
    upstream_price_in: Decimal | None
    upstream_price_out: Decimal | None
    db_price_in: Decimal | None
    db_price_out: Decimal | None

    def as_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "ok": self.ok,
            "note": self.note,
            "upstream_price_in_per_mtok": (
                str(self.upstream_price_in) if self.upstream_price_in is not None else None
            ),
            "upstream_price_out_per_mtok": (
                str(self.upstream_price_out) if self.upstream_price_out is not None else None
            ),
            "db_price_in_per_mtok": (
                str(self.db_price_in) if self.db_price_in is not None else None
            ),
            "db_price_out_per_mtok": (
                str(self.db_price_out) if self.db_price_out is not None else None
            ),
        }


def fetch_openrouter_catalog(http: httpx.Client | None = None) -> dict[str, dict]:
    """Return {model_id: model_payload} keyed by the OpenRouter slug
    (without the ``openrouter:`` ModelEntry prefix)."""
    client = http or httpx.Client(timeout=30.0)
    try:
        resp = client.get(OPENROUTER_MODELS_URL)
        resp.raise_for_status()
        payload = resp.json()
    finally:
        if http is None:
            client.close()
    return {m["id"]: m for m in payload.get("data", [])}


def _per_token_to_per_mtok(value: str | float | None) -> Decimal | None:
    if value is None or value == "":
        return None
    return (Decimal(str(value)) * Decimal(1_000_000)).quantize(Decimal("0.0001"))


def verify_models(
    model_ids: Iterable[str] | None = None,
    *,
    http: httpx.Client | None = None,
    stamp: bool = True,
) -> list[VerificationResult]:
    """Verify pricing for a subset of OpenRouter ModelEntry rows.

    Args:
        model_ids: full ModelEntry ids (e.g. "openrouter:openai/gpt-oss-120b:free").
            None ⇒ verify every active OpenRouter row.
        stamp: when True, write last_verified_at + last_verified_note to the DB.
    """
    qs = ModelEntry.objects.filter(is_active=True, provider="openrouter")
    if model_ids is not None:
        qs = qs.filter(id__in=list(model_ids))
    rows = list(qs)
    if not rows:
        return []

    catalog = fetch_openrouter_catalog(http=http)
    results: list[VerificationResult] = []
    now = timezone.now()
    for row in rows:
        slug = row.id.removeprefix("openrouter:")
        upstream = catalog.get(slug)
        if upstream is None:
            note = f"slug {slug!r} not in OpenRouter /api/v1/models response"
            result = VerificationResult(
                model_id=row.id,
                ok=False,
                note=note,
                upstream_price_in=None,
                upstream_price_out=None,
                db_price_in=row.price_in_per_mtok,
                db_price_out=row.price_out_per_mtok,
            )
        else:
            pricing = upstream.get("pricing") or {}
            up_in = _per_token_to_per_mtok(pricing.get("prompt"))
            up_out = _per_token_to_per_mtok(pricing.get("completion"))
            db_in = row.price_in_per_mtok or Decimal("0")
            db_out = row.price_out_per_mtok or Decimal("0")
            up_in_cmp = up_in if up_in is not None else Decimal("0")
            up_out_cmp = up_out if up_out is not None else Decimal("0")
            ok = (
                abs(up_in_cmp - db_in) <= PRICE_TOLERANCE
                and abs(up_out_cmp - db_out) <= PRICE_TOLERANCE
            )
            if ok:
                note = ""
            else:
                note = (
                    f"pricing drift: db ${db_in}/${db_out} per Mtok vs "
                    f"upstream ${up_in_cmp}/${up_out_cmp}"
                )
            result = VerificationResult(
                model_id=row.id,
                ok=ok,
                note=note,
                upstream_price_in=up_in,
                upstream_price_out=up_out,
                db_price_in=row.price_in_per_mtok,
                db_price_out=row.price_out_per_mtok,
            )
        if stamp:
            row.last_verified_at = now
            row.last_verified_note = result.note
            row.save(update_fields=["last_verified_at", "last_verified_note"])
        results.append(result)
    return results
