"""Curated per-preset model menus.

The expandable per-agent model selector is scoped to the active preset's
menu. dev/frugal menus are sourced from a maintained OpenRouter slug
allowlist and refreshed live by fetching.sync_tier_models(); the other
three menus are static lists of already-seeded models.

This module is imported by seed.py, so it must not import Django models.
"""
from __future__ import annotations

import logging
from decimal import Decimal

log = logging.getLogger(__name__)

# --- dev: OpenRouter :free slugs only. sync_tier_models() drops any that
#     come back non-free (budget guard). Curated to LIVE, NON-REASONING routes:
#     reasoning slugs (gpt-oss, qwen3*) burn the budget on hidden thinking and
#     emit empty content (the qwen3.6-27b hang, runs 236/237); the removed
#     arcee-ai/trinity-large-thinking:free and minimax/minimax-m2.5:free 404'd
#     upstream (runs 228-235). The free non-reasoning pool is small, so the
#     dev preset spreads its 8 personas across these (some reused) rather than
#     1-per-persona. test_preset_invariants enforces non-reasoning + vetted.
DEV_TIER_SLUGS: list[str] = [
    "nvidia/nemotron-3-super-120b-a12b:free",
    "deepseek/deepseek-v4-flash:free",
    "google/gemma-4-31b-it:free",
    "z-ai/glm-4.5-air:free",
]

# --- frugal: cheap OpenRouter PAID slugs (stable, non-ephemeral). All
#     NON-REASONING — the reasoning routes (openai/gpt-oss-120b,
#     qwen/qwen3-235b-a22b-2507, qwen/qwen3.6-27b) were removed because they
#     reasoning-exhaust to empty content on the cheap tier. amazon/nova-lite-v1
#     was dropped after a validation run degraded 4 of its council agents
#     (it returns non-schema-conforming output, not empty content, so it can't
#     even self-heal-hop). sync_tier_models() drops any above the
#     FRUGAL_PRICE_CEILING_* bounds.
FRUGAL_TIER_SLUGS: list[str] = [
    "meta-llama/llama-3.3-70b-instruct",
    "nvidia/nemotron-3-nano-30b-a3b",
    "mistralai/mistral-small-3.2-24b-instruct",
    "google/gemma-3-27b-it",
    "z-ai/glm-4-32b",
    "deepseek/deepseek-v4-flash",
]

# --- reasoning models — a curated OpenRouter allowlist of dedicated
#     chain-of-thought routes (per openrouter._REASONING_SLUGS). Offered in the
#     research/quality menus only, for users who want reasoning depth on the
#     ANALYTICAL roles. They are NEVER wired as a decision-role default: the
#     personas/PM/RM/CIO path stays Anthropic-anchored because reasoning models
#     can reasoning-exhaust to empty content (the qwen3.6-27b hang, runs 236/237
#     — see test_preset_invariants). Unlike DEV/FRUGAL these are NOT refreshed by
#     sync_tier_models(); they're statically priced in seed.py and kept fresh by
#     `verify_openrouter_pricing` (verify_models audits every active OpenRouter
#     row), which is what the §9.15 freshness invariant requires.
REASONING_TIER_SLUGS: list[str] = [
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",  # free reasoning
    "deepseek/deepseek-v4-pro",                            # flagship reasoning
    "z-ai/glm-5.1",                                        # mid reasoning
]

_REASONING_MENU_IDS = [f"openrouter:{s}" for s in REASONING_TIER_SLUGS]

# --- static menus for the Anthropic-anchored tiers (decision #4). These
#     reference existing ModelEntry ids; not fetched. Any "openrouter:" id
#     listed here MUST be in a curated allowlist whose pricing stays fresh —
#     DEV/FRUGAL_TIER_SLUGS (live sync) or REASONING_TIER_SLUGS (verify_models)
#     — see the static-menu invariant in §9.15.
STATIC_TIER_MENUS: dict[str, list[str]] = {
    "research": [
        "anthropic:claude-sonnet-4-6",
        "anthropic:claude-haiku-4-5-20251001",
        "anthropic:claude-opus-4-7",
        *_REASONING_MENU_IDS,
    ],
    "quality": [
        "anthropic:claude-opus-4-7",
        "anthropic:claude-sonnet-4-6",
        "anthropic:claude-haiku-4-5-20251001",
        *_REASONING_MENU_IDS,
    ],
    "hybrid": [
        "anthropic:claude-sonnet-4-6",
        "anthropic:claude-haiku-4-5-20251001",
        "openrouter:meta-llama/llama-3.3-70b-instruct",
    ],
}

# frugal price guard — USD per Mtok. A slug whose live pricing exceeds
# either bound is excluded from the menu and flagged by the sync. Sits
# above the priciest legitimately-curated frugal slug (qwen/qwen3.6-27b
# at ~0.30 in / ~3.20 out per seed.py) while still catching a jump toward
# frontier pricing (Sonnet 3/15, Opus 15/75). If you allowlist a pricier
# model, raise these — test §9.13 guards it.
FRUGAL_PRICE_CEILING_IN = Decimal("1.00")
FRUGAL_PRICE_CEILING_OUT = Decimal("5.00")


def _default_menu(preset: str) -> list[str]:
    """The baseline menu from the DEFAULT_* constants — used as the cold-start
    fallback when a tier has no DB membership yet (or the DB is unreachable
    pre-migrate). dev/frugal resolve to "openrouter:"-prefixed allowlist ids; the
    others return their static list. Unknown preset -> []."""
    if preset == "dev":
        return [f"openrouter:{s}" for s in DEV_TIER_SLUGS]
    if preset == "frugal":
        return [f"openrouter:{s}" for s in FRUGAL_TIER_SLUGS]
    return list(STATIC_TIER_MENUS.get(preset, []))


def tier_menu(preset: str) -> list[str]:
    """Ordered ModelEntry ids for a preset's menu.

    Reads the DB `TierMembership` for the tier, filtered to ACTIVE models — so a
    deactivated/delisted model auto-drops from every menu (the core resilience
    behavior). Falls back to the baseline constants only when the tier is
    UNSEEDED (no membership rows) or the DB is unavailable. A seeded tier whose
    members are all inactive returns [] (honest), not the stale baseline.
    Unknown preset -> [].
    """
    try:
        from .models import TierMembership
        rows = list(
            TierMembership.objects.filter(tier_id=preset)
            .order_by("ordering", "model_id")
            .values_list("model_id", "model__is_active")
        )
    except Exception:
        # Degrade to the baseline (cold-start / DB-down), but surface a genuine
        # bug rather than swallow it silently.
        log.warning("tier_menu(%r): DB read failed, using baseline", preset, exc_info=True)
        return _default_menu(preset)
    if not rows:
        return _default_menu(preset)
    return [mid for mid, active in rows if active]


def tier_default(preset: str) -> str | None:
    """The tier's default model id — the resilience fallback target.

    `TierConfig.default_model` when it's still active, else the first active menu
    member, else None.
    """
    try:
        from .models import TierConfig
        tc = (
            TierConfig.objects.filter(tier_name=preset)
            .select_related("default_model")
            .first()
        )
    except Exception:
        tc = None
    if tc and tc.default_model_id and tc.default_model and tc.default_model.is_active:
        return tc.default_model_id
    menu = tier_menu(preset)
    return menu[0] if menu else None


# Tier preference order when a dead model id must resolve its own tier (no
# preset context): cheap tiers first so healing never silently escalates cost.
_TIER_PREFERENCE = ("frugal", "dev", "hybrid", "research", "quality")

# The final rung when no tier menu yields a live candidate. Provider-qualified
# form of settings.LLM_LAST_RESORT_MODEL (the runtime L2 target), so selection
# healing and runtime healing converge on the same known-good model.
def _last_resort_id() -> str:
    try:
        from django.conf import settings
        slug = getattr(
            settings, "LLM_LAST_RESORT_MODEL", "meta-llama/llama-3.3-70b-instruct"
        )
    except Exception:
        slug = "meta-llama/llama-3.3-70b-instruct"
    return slug if ":" in slug.split("/", 1)[0] else f"openrouter:{slug}"


def _provider_allowed(model_id: str) -> bool:
    """False for candidates whose provider cannot run in this environment
    (currently: `anthropic:` ids under BLOCK_ANTHROPIC). Healing a dead pick
    onto a blocked provider would just move the failure downstream."""
    if not str(model_id).startswith("anthropic:"):
        return True
    try:
        from django.conf import settings
        return not getattr(settings, "BLOCK_ANTHROPIC", False)
    except Exception:
        return True


def _replacement_pool(preset: str | None, fallback: str | None, dead: set[str]) -> list[str]:
    """Ordered live candidates for a tier: explicit active `fallback` first, then
    the tier default, then the remaining active menu members in menu order —
    minus dead ids and provider-blocked ids, deduplicated."""
    pool: list[str] = []
    if fallback:
        pool.append(str(fallback))
    default = tier_default(preset or "")
    if default:
        pool.append(default)
    pool.extend(tier_menu(preset or ""))
    seen: set[str] = set()
    out: list[str] = []
    for mid in pool:
        if mid in seen or mid in dead or not _provider_allowed(mid):
            continue
        seen.add(mid)
        out.append(mid)
    return out


def _tiers_for_model(model_id: str) -> list[str]:
    """Tier names whose membership contains `model_id`, in _TIER_PREFERENCE
    order (unknown ids -> []). Accepts bare slugs (membership ids are
    provider-qualified, so a bare slug also matches its `openrouter:` form)."""
    candidates = {model_id}
    if ":" not in model_id.split("/", 1)[0]:
        candidates.add(f"openrouter:{model_id}")
    try:
        from .models import TierMembership
        tiers = set(
            TierMembership.objects.filter(model_id__in=candidates).values_list(
                "tier_id", flat=True
            )
        )
    except Exception:
        return []
    ordered = [t for t in _TIER_PREFERENCE if t in tiers]
    return ordered + sorted(tiers - set(ordered))


def heal_overrides(
    overrides: dict[str, str],
    *,
    preset: str | None = None,
    fallback: str | None = None,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Rewrite every override pointing at a DEAD catalog model (inactive OR
    unknown id) to a live same-tier model. Returns ``(healed, moves)`` where
    ``moves`` records each substitution as ``{"agent", "from", "to"}``.

    This is the selection-time self-heal core (the runtime adapter chain is the
    last net). Properties:

    - Active picks never move; ``ollama:`` ids are exempt (no ModelEntry row by
      design — they validate against live discovery instead).
    - Dead picks are SPREAD deterministically across the tier's active menu
      rather than all collapsing onto one model (the all-16-agents-on-one-slug
      worker-saturation lesson): dead agents are processed in sorted order and
      assigned pool[i % len(pool)]. A single dead pick therefore lands on the
      explicit ``fallback`` / tier default — the pre-existing behavior.
    - With no ``preset`` context (stored strategy/schedule/graph maps), each
      dead id resolves its own tier via TierMembership (cheap tiers first),
      degrading to ``settings.LLM_DEFAULT_PRESET``'s menu, then the last-resort
      model — so healing works for any map, however it was produced.
    - Provider-blocked candidates (anthropic: under BLOCK_ANTHROPIC) are never
      chosen. On DB failure the map passes through unchanged (never crash a
      dispatch path).
    """
    if not overrides:
        return overrides, []
    catalog_ids = {
        str(m) for m in overrides.values() if not str(m).startswith("ollama:")
    }
    if not catalog_ids:
        return overrides, []
    check_ids = set(catalog_ids)
    if fallback and not str(fallback).startswith("ollama:"):
        check_ids.add(str(fallback))
    # Bare slugs ("meta-llama/llama-3.3-70b-instruct") are legal API input and
    # must match their provider-qualified ModelEntry row — probe the qualified
    # variant of every bare input so an active bare pick is never misclassified
    # as dead.
    bare_ids = {mid.split(":", 1)[-1] for mid in check_ids}
    qualified_variants = {f"openrouter:{b}" for b in bare_ids}
    try:
        from django.db.models import Q

        from .models import ModelEntry
        active = set(
            ModelEntry.objects.filter(is_active=True)
            .filter(
                Q(id__in=check_ids)
                | Q(id__in=bare_ids)
                | Q(id__in=qualified_variants)
            )
            .values_list("id", flat=True)
        )
    except Exception:
        # On the run-dispatch path: never crash, but log so a real bug here
        # (not just DB-down) is discoverable rather than silently no-op'd.
        log.warning(
            "heal_overrides(%r): DB read failed, passing through",
            preset, exc_info=True,
        )
        return overrides, []
    active_short = {a.split(":", 1)[-1] for a in active}

    def _is_live(mid: str) -> bool:
        return mid in active or mid.split(":", 1)[-1] in active_short

    dead = {mid for mid in catalog_ids if not _is_live(mid)}
    if not dead:
        return overrides, []
    live_fallback = fallback if (fallback and _is_live(str(fallback))) else None

    # Build one pool per tier context, lazily. With a preset the whole map heals
    # from that tier; without one, each dead id picks its own tier.
    pools: dict[str, list[str]] = {}

    def _pool_for(dead_id: str) -> list[str]:
        if preset:
            keys = [preset]
        else:
            keys = _tiers_for_model(dead_id)
            if not keys:
                try:
                    from django.conf import settings
                    keys = [getattr(settings, "LLM_DEFAULT_PRESET", "frugal")]
                except Exception:
                    keys = ["frugal"]
        for key in keys:
            if key not in pools:
                pools[key] = _replacement_pool(key, live_fallback, dead)
            if pools[key]:
                return pools[key]
        return []

    healed = dict(overrides)
    moves: list[dict[str, str]] = []
    dead_agents = sorted(
        a for a, m in overrides.items() if str(m) in dead
    )
    for i, agent in enumerate(dead_agents):
        old = str(overrides[agent])
        pool = _pool_for(old)
        if not pool:
            last = _last_resort_id()
            pool = [last] if last not in dead and _provider_allowed(last) else []
        if not pool:
            continue  # nothing live to offer — runtime chain remains the net
        new = pool[i % len(pool)]
        healed[agent] = new
        moves.append({"agent": agent, "from": old, "to": new})
    if moves:
        log.warning(
            "heal_overrides(%r): healed %d dead model reference(s): %s",
            preset, len(moves),
            "; ".join(f"{m['agent']}: {m['from']} -> {m['to']}" for m in moves),
        )
    return healed, moves


def sanitize_overrides(
    preset: str | None,
    overrides: dict[str, str],
    *,
    fallback: str | None = None,
) -> dict[str, str]:
    """Rewrite any override pointing at an INACTIVE catalog model to a live
    same-tier model, leaving active picks untouched (see ``heal_overrides`` —
    this is the map-only convenience wrapper used on dispatch paths).

    A single dead pick degrades to the explicit `fallback` / tier default
    exactly as before; multiple dead picks now SPREAD across the tier's active
    menu instead of collapsing onto one model. `ollama:` ids are exempt. If
    nothing live can be found, the map is returned unchanged (the runtime
    L1-L3 self-heal remains the last net).
    """
    healed, _moves = heal_overrides(overrides, preset=preset, fallback=fallback)
    return healed


def anchor_non_personas(
    preset: str | None,
    overrides: dict[str, str],
    tier_choice: str | None,
) -> dict[str, str]:
    """Set every NON-persona role to `tier_choice` (the per-tier default),
    leaving the personas' spread intact. No-op for 'hybrid' (whose non-persona
    roles are intentionally local/Sonnet) or when `tier_choice` is falsy."""
    if not tier_choice or preset == "hybrid":
        return overrides
    from .presets import PERSONA_AGENTS
    return {
        agent: (mid if agent in PERSONA_AGENTS else tier_choice)
        for agent, mid in overrides.items()
    }
