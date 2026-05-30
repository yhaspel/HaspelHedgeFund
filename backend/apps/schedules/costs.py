"""Cost estimation + preset degradation for scheduled runs (P3b).

Reuses the per-agent token assumptions (``PER_AGENT_TOKEN_ESTIMATES``) and the
``ModelEntry`` price table that the portfolio-cycle estimator uses, so a
scheduled run's pre-flight estimate is consistent with the rest of the app.
"""
from __future__ import annotations

from apps.models_catalog.presets import PERSONA_AGENTS, PRESETS, expand_preset
from apps.portfolios.tasks import PER_AGENT_TOKEN_ESTIMATES

# Cheapest-last. Degradation walks toward the end of this list. "dev" (free
# OpenRouter slugs) and "frugal" (cheap OpenRouter) are the cheap tiers.
DEGRADE_CHAIN = ["quality", "research", "hybrid", "frugal", "dev"]


def cheaper_preset(preset: str) -> str | None:
    """Next-cheaper preset, or None if already cheapest. Unknown/custom presets
    jump to 'frugal'."""
    if preset in DEGRADE_CHAIN:
        i = DEGRADE_CHAIN.index(preset)
        return DEGRADE_CHAIN[i + 1] if i + 1 < len(DEGRADE_CHAIN) else None
    return "frugal"


def _resolve_local_tier_a(user, preset: str) -> str | None:
    """Discover the user's best local (Ollama) model when the preset uses the
    ``<local-tier-a>`` token (only 'hybrid' does). Mirrors the portfolio-cycle
    resolver; returns None when no local host is configured."""
    raw = PRESETS.get(preset, {})
    if "<local-tier-a>" not in raw.values():
        return None
    try:
        from apps.models_catalog.models import ProviderKey
        from apps.models_catalog.ollama_discovery import discover_ollama_models

        pk = ProviderKey.objects.filter(user=user).first()
        host = pk.ollama_host if pk else ""
        if not host:
            return None
        discovered = discover_ollama_models(host)
        return next(
            (m["id"] for m in discovered if (m.get("notes") or "").startswith("local-A")),
            None,
        ) or next((m["id"] for m in discovered), None)
    except Exception:  # noqa: BLE001 — discovery is best-effort
        return None


def resolve_overrides(user, preset: str, explicit_overrides: dict | None) -> dict[str, str]:
    """Per-agent ``{agent: model_id}`` map for a scheduled run: expand the preset
    (resolving ``<local-tier-a>``) then layer any explicit per-agent overrides."""
    base = expand_preset(preset, local_tier_a=_resolve_local_tier_a(user, preset))
    if explicit_overrides:
        base = {**base, **explicit_overrides}
    return base


def _agents_for(personas: list[str] | None) -> list[str]:
    """The agents a single-ticker ad-hoc run invokes: the selected personas (or
    all when empty) plus every non-persona agent (analytical + macro + news +
    risk + pm + cio)."""
    chosen = set(personas) if personas else set(PERSONA_AGENTS)
    agents = []
    for a in PER_AGENT_TOKEN_ESTIMATES:
        if a in PERSONA_AGENTS and a not in chosen:
            continue
        agents.append(a)
    return agents


def estimate_run_cost(
    user, preset: str, explicit_overrides: dict | None, personas: list[str] | None, n_tickers: int
) -> dict:
    """Projected USD spend for ``n_tickers`` single-ticker council runs.

    Returns ``{overrides, per_run_usd, est_total_usd}``. A model with no
    ``ModelEntry`` price row contributes $0 (local/free models) — so the
    estimate is a floor for unpriced models and accurate for priced ones.
    """
    from apps.models_catalog.models import ModelEntry

    overrides = resolve_overrides(user, preset, explicit_overrides)
    prices = {m.id: m for m in ModelEntry.objects.all()}
    per_run = 0.0
    for agent in _agents_for(personas):
        tin, tout = PER_AGENT_TOKEN_ESTIMATES.get(agent, (8000, 1000))
        m = prices.get(overrides.get(agent))
        pin = float(m.price_in_per_mtok or 0) if m else 0.0
        pout = float(m.price_out_per_mtok or 0) if m else 0.0
        per_run += (tin * pin + tout * pout) / 1_000_000
    return {
        "overrides": overrides,
        "per_run_usd": round(per_run, 4),
        "est_total_usd": round(per_run * max(n_tickers, 0), 4),
    }
