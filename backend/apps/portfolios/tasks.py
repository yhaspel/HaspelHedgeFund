"""daily_long_short_cycle — autonomous L/S orchestration.

Pipeline:
  1. Load PortfolioStrategy, active UniverseMembership on as_of_date.
  2. Run Screener (Python features only — no LLM in this MVP).
  3. Persist ScreenerRanking + create PortfolioTarget(status=running).
  4. Estimate cost; if over `cost_ceiling_per_cycle_usd`, trim top_k_* until OK.
  5. Build Borrow quotes for every short candidate.
  6. Dispatch Celery chord: one `run_candidate_council` per (ticker, side),
     callback `finalize_cycle`.
  7. Callback collects decisions → Portfolio Constructor → Rebalancer
     → persist RebalanceOrder rows → mark target done.

Idempotency: re-running with the same (strategy_id, as_of_date) reuses an
existing `done` PortfolioTarget instead of producing a new one. Re-running
with `force=True` resets it.
"""
from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import datetime
from decimal import Decimal

from celery import chord, shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.data.providers.factory import (
    get_edgar_provider,
    get_fmp_provider,
    get_news_service,
    get_ownership_provider,
)
from apps.models_catalog.presets import PRESETS, expand_preset
from apps.models_catalog.tier_menus import anchor_non_personas, sanitize_overrides
from hedgefund_agents.graphs.council import build_council_graph, build_sector_council_graph
from hedgefund_agents.screener.screener_agent import ScreenerAbort, run_screener
from hedgefund_agents.screener.sector_features import macro_regime_vector, run_sector_screener

from .beta import compute_betas_for
from .borrow import StubBorrowProvider
from .construction import (
    Candidate,
    Constraints,
    construct,
    construct_concentrated_long,
    construct_global_macro,
    construct_market_neutral,
    construct_risk_parity,
    construct_sector_rotation,
)
from .models import (
    Pair,
    PairZHistory,
    PortfolioStrategy,
    PortfolioTarget,
    Position,
    RebalanceOrder,
    ScreenerRanking,
    UniverseMembership,
)
from .pairs import (
    _ols_alpha_beta,
    decide_open_pair_action,
    gather_log_prices,
    screen_pairs,
)
from .rebalance import CurrentPosition, RebalanceConfig, compute_orders
from .vol import compute_vols_for

log = logging.getLogger(__name__)


def _resolve_as_of(s: str | None) -> date_cls:
    if not s:
        return timezone.now().date()
    if isinstance(s, date_cls):
        return s
    return datetime.fromisoformat(str(s)).date()


def _resolve_cycle_target(
    strategy: PortfolioStrategy,
    as_of: date_cls,
    *,
    defaults: dict,
    supersedes_target_id: int | None = None,
) -> PortfolioTarget:
    """Resolve the PortfolioTarget row a cycle run should write into.

    Two modes:

    * Normal (``supersedes_target_id is None``) — preserve the legacy
      single-row-per-day semantics: reuse the most recent *non-superseded* row
      for (strategy, as_of) if one exists, else create a fresh row. Scoping to
      non-superseded rows is what keeps this unambiguous after a rerun has left
      an older terminal row behind (P4 WS-B), so we never hit
      ``MultipleObjectsReturned`` the way a bare ``update_or_create`` would.

    * Rerun (``supersedes_target_id`` set) — leave the old terminal row in
      place and create a brand-new row, stamping ``old.superseded_by = new`` so
      the cycles list can render a "↻ superseded" pill. Idempotent across the
      multiple creation sites a single cycle has (e.g. the pairs flavor creates
      an early audit row then a final row): if the old row already points at a
      fresh row, that row is reused instead of creating a second one.
    """
    if supersedes_target_id is not None:
        old = PortfolioTarget.objects.filter(pk=supersedes_target_id).first()
        if old is not None and old.superseded_by_id:
            target = old.superseded_by
            for key, value in defaults.items():
                setattr(target, key, value)
            target.save()
            return target
        target = PortfolioTarget.objects.create(
            strategy=strategy, as_of_date=as_of, **defaults
        )
        if old is not None:
            PortfolioTarget.objects.filter(pk=old.pk).exclude(pk=target.pk).update(
                superseded_by=target
            )
        return target

    target = (
        PortfolioTarget.objects.filter(
            strategy=strategy, as_of_date=as_of, superseded_by__isnull=True,
        )
        .order_by("-created_at")
        .first()
    )
    if target is not None:
        for key, value in defaults.items():
            setattr(target, key, value)
        target.save()
        return target
    return PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=as_of, **defaults
    )


def _maybe_auto_enroll(target: PortfolioTarget) -> None:
    """P4 WS-E: if the strategy opted into auto-enroll, materialize the just-
    completed cycle into its book. Broad try/except — a transient enrollment
    failure (e.g. a stale mark) must never flip a successful cycle to failed."""
    try:
        strategy = target.strategy
        # P7: a broker-linked strategy's book of record is its broker account
        # (real fills), not a simulated Position book — never enroll it.
        from apps.brokers.models import StrategyBrokerLink

        if StrategyBrokerLink.objects.filter(
            strategy=strategy, is_active=True
        ).exists():
            return
        if not getattr(strategy, "auto_enroll_on_done", False):
            return
        from . import runs_bridge

        runs_bridge.enroll_target_into_portfolio(target, mode="auto")
    except Exception:  # pragma: no cover — defensive; logged, never re-raised
        log.exception("auto_enroll_failed target_id=%s", target.pk)


def _resolve_model_overrides(
    strategy: PortfolioStrategy,
    *,
    preset: str | None = None,
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """Order of precedence:
      0. A transient per-cycle choice from the `Run cycle now` dispatch modal
         (P4c): an explicit `overrides` map wins outright; otherwise an explicit
         `preset` name is expanded, bypassing the user/strategy resolution
         below. Both apply to this dispatch only — they never mutate saved
         preferences.
      1. User's per-agent defaults from Settings → Models (the "Default model"
         selector populates this with the same model for every agent).
      2. The strategy's model_preset (e.g. 'frugal', 'hybrid') expanded into a
         per-agent map.
      3. settings.LLM_DEFAULT_PRESET — the env-gated fallback for a strategy
         that never deliberately picked a preset. Production resolves this to
         'hybrid'; the dev settings module pins it to 'dev' so local
         development can't leak Anthropic frontier spend.
      4. Empty dict → registry DEFAULT_MODELS applies.

    `model_preset` carries a DB-level default of "hybrid", so a strategy that
    was never given a deliberate preset is indistinguishable from one that
    explicitly chose "hybrid". When the environment default is itself a
    non-frontier preset (dev), that bare "hybrid" is treated as "unchosen" and
    falls through to the env default — otherwise dev strategies created before
    this guard would keep resolving to the frontier `hybrid` map.
    """
    # Explicit dispatch-modal overrides are already validated active at the API
    # boundary (validate_model_overrides), so they pass through verbatim.
    if overrides:
        return dict(overrides)
    default_preset = getattr(settings, "LLM_DEFAULT_PRESET", "hybrid")
    user_prefs = getattr(strategy.user, "model_prefs", None)
    # Saved per-agent picks layer ON TOP of the preset+per-tier base (a PARTIAL
    # map must not drop the other roles to the registry default). A transient
    # dispatch-modal preset (explicit `preset`) ignores saved per-agent prefs.
    per_agent: dict[str, str] = {}
    if preset is None:
        if user_prefs and user_prefs.per_agent_defaults:
            per_agent = dict(user_prefs.per_agent_defaults)
        preset = strategy.model_preset or default_preset
        if preset == "hybrid" and default_preset != "hybrid":
            preset = default_preset

    # P3-C §12.3: when the resolved preset actually uses the <local-tier-a>
    # token, discover the user's local models and pass the best local-A
    # entry to expand_preset. Probe only when needed — the rest of the
    # presets get an empty-cost no-op fallback.
    local_a: str | None = None
    raw_rules = PRESETS.get(preset, {})
    if "<local-tier-a>" in raw_rules.values():
        from apps.models_catalog.models import ProviderKey
        from apps.models_catalog.ollama_discovery import discover_ollama_models

        pk = ProviderKey.objects.filter(user=strategy.user).first()
        host = pk.ollama_host if pk else ""
        if host:
            discovered = discover_ollama_models(host)
            local_a = next(
                (m["id"] for m in discovered if (m.get("notes") or "").startswith("local-A")),
                None,
            ) or next((m["id"] for m in discovered), None)

    preset_map = expand_preset(preset, local_tier_a=local_a)
    if not preset_map:
        # No preset recipe (unknown preset) — fall back to the user's explicit
        # per-agent picks alone.
        return sanitize_overrides(preset, per_agent) if per_agent else {}
    # The per-tier default anchors the non-persona (analytical + orchestration)
    # roles; personas keep the spread. Skipped for hybrid, whose non-persona
    # roles are intentionally local/Sonnet (anchoring would defeat it).
    tier_choice = _user_tier_default(strategy.user, preset) if preset != "hybrid" else None
    preset_map = anchor_non_personas(preset, preset_map, tier_choice)
    # Saved per-agent picks win over the preset/per-tier base.
    if per_agent:
        preset_map = {**preset_map, **per_agent}
    # Sanitize: a deactivated pick (delisted upstream) degrades to the tier
    # default instead of reaching the run as a dead id — only inactive entries
    # move, the persona spread is kept.
    return sanitize_overrides(preset, preset_map, fallback=tier_choice or None)


def _user_tier_default(user, preset: str | None) -> str | None:
    """The user's saved default model for `preset`'s tier, if any."""
    prefs = getattr(user, "model_prefs", None)
    if prefs and isinstance(prefs.per_tier_defaults, dict):
        return prefs.per_tier_defaults.get(preset)
    return None


def _active_members(strategy: PortfolioStrategy, as_of: date_cls) -> list[tuple[str, str]]:
    qs = UniverseMembership.objects.filter(
        universe=strategy.universe,
        effective_from__lte=as_of,
    ).filter(Q(effective_to__isnull=True) | Q(effective_to__gt=as_of))
    return [(m.ticker, m.sector) for m in qs]


PER_AGENT_TOKEN_ESTIMATES = {
    "buffett": (10000, 1500), "munger": (10000, 1500), "graham": (10000, 1500),
    "wood": (10000, 1500), "druckenmiller": (10000, 1500), "burry": (10000, 1500),
    "damodaran": (10000, 1500), "lynch": (10000, 1500),
    "fundamentals": (7000, 800), "technicals": (7000, 800),
    "valuation": (8000, 1000), "sentiment": (5000, 600),
    "macro": (5000, 1000), "news_digest": (15000, 1500),
    "risk_manager": (15000, 1500), "portfolio_manager": (20000, 1500),
    "cio": (12000, 1000),
}


def estimate_cycle(
    strategy: PortfolioStrategy,
    *,
    override_preset: str | None = None,
    override_models: dict[str, str] | None = None,
) -> dict:
    """Pre-flight cost estimate for a manual `Run cycle now`.

    Returns the resolved per-agent model map plus the projected USD spend
    for `top_k_longs + top_k_shorts` council invocations. CIO is excluded
    because the cycle disables it (disable_cio=True).

    `override_preset` / `override_models` (P4c dispatch modal): when set, the
    estimate reflects that transient choice instead of the strategy's saved
    resolution — `override_models` wins, else `override_preset` is expanded.
    The returned `preset` echoes the chosen tier so the modal header/menu stay
    in sync with the table below."""
    from apps.models_catalog.models import ModelEntry

    overrides = _resolve_model_overrides(
        strategy, preset=override_preset, overrides=override_models
    )
    prices = {m.id: m for m in ModelEntry.objects.all()}
    n = int(strategy.top_k_longs) + int(strategy.top_k_shorts)
    per_agent = []
    per_call_cost = 0.0
    for agent, (tin, tout) in PER_AGENT_TOKEN_ESTIMATES.items():
        if agent == "cio":  # disabled in the cycle
            continue
        model_id = overrides.get(agent)
        m = prices.get(model_id) if model_id else None
        pin = float(m.price_in_per_mtok or 0) if m else 0.0
        pout = float(m.price_out_per_mtok or 0) if m else 0.0
        c = (tin * pin + tout * pout) / 1_000_000
        per_call_cost += c
        per_agent.append({
            "agent": agent,
            "model": model_id or "(registry default)",
            "model_name": m.display_name if m else "",
            "tier": m.tier if m else "",
            "per_call_usd": round(c, 4),
        })
    return {
        "n_candidates": n,
        "per_call_usd": round(per_call_cost, 4),
        "est_total_usd": round(per_call_cost * n, 2),
        "cost_ceiling_usd": float(strategy.cost_ceiling_per_cycle_usd),
        "exceeds_ceiling": (per_call_cost * n) > float(strategy.cost_ceiling_per_cycle_usd),
        "per_agent": per_agent,
        "overrides": overrides,
        "preset": override_preset or strategy.model_preset,
    }


def _per_council_call_cost(
    strategy: PortfolioStrategy,
    *,
    preset: str | None = None,
    overrides: dict[str, str] | None = None,
) -> float:
    """P02e review: derive the per-council-call cost from the same model
    catalog estimate the API endpoint shows. Replaces the prior hardcoded
    $0.05 / call so trimming and the pre-flight estimate agree.

    `preset` / `overrides` carry a transient per-cycle choice (P4c dispatch
    modal) so the budget trim prices the SAME models the estimate showed —
    otherwise a re-tiered cycle would trim against the strategy's saved preset.

    Falls back to $0.05 only when no ModelEntry rows are available (fresh
    install / unseeded test DB).
    """
    from apps.models_catalog.models import ModelEntry

    overrides = _resolve_model_overrides(strategy, preset=preset, overrides=overrides)
    prices = {m.id: m for m in ModelEntry.objects.all()}
    if not prices:
        return 0.05
    per_call = 0.0
    for agent, (tin, tout) in PER_AGENT_TOKEN_ESTIMATES.items():
        if agent == "cio":  # disabled in the cycle
            continue
        model_id = overrides.get(agent)
        m = prices.get(model_id) if model_id else None
        pin = float(m.price_in_per_mtok or 0) if m else 0.0
        pout = float(m.price_out_per_mtok or 0) if m else 0.0
        per_call += (tin * pin + tout * pout) / 1_000_000
    return per_call or 0.05


def _trim_k_for_budget(
    strategy: PortfolioStrategy, n_longs: int, n_shorts: int,
    *, preset: str | None = None, overrides: dict[str, str] | None = None,
) -> tuple[int, int]:
    """Trim symmetrically until the projected cost fits inside
    ``cost_ceiling_per_cycle_usd``. Uses the same model-catalog estimate as
    the pre-flight endpoint — including any transient per-cycle override
    (P4c) — so the displayed budget and the actual trim decision agree
    (P02e review fix)."""
    per_call = _per_council_call_cost(strategy, preset=preset, overrides=overrides)
    cap = float(strategy.cost_ceiling_per_cycle_usd)
    if cap <= 0 or per_call <= 0:
        return 0, 0
    max_total = int(cap / per_call)
    if n_longs + n_shorts <= max_total:
        return n_longs, n_shorts
    # Keep the long/short ratio.
    if n_longs + n_shorts == 0:
        return 0, 0
    long_share = n_longs / (n_longs + n_shorts)
    new_longs = max(1, int(round(max_total * long_share)))
    new_shorts = max(0, max_total - new_longs)
    return new_longs, new_shorts


@shared_task(
    bind=True,
    # L3: per-candidate wall-clock cap (same rationale as execute_run) so one
    # stalled council in a cycle can't park a worker slot indefinitely.
    soft_time_limit=getattr(settings, "RUN_SOFT_TIME_LIMIT_SECONDS", 600),
    time_limit=getattr(settings, "RUN_HARD_TIME_LIMIT_SECONDS", 720),
)
def run_candidate_council(self, payload: dict) -> dict:
    """Sub-task: run the full council on one candidate.

    Returns a dict suitable for the chord callback (the decision + side metadata).

    P2l: if `payload['run_id']` is set, claim that Run row, mark it running,
    snapshot agent versions, and persist AgentMessage/Decision rows when the
    graph completes. The same return contract as before is preserved so
    finalize_cycle keeps working.
    """
    from apps.runs.models import Run
    from hedgefund_agents.personas import ALL_PERSONAS
    from hedgefund_agents.versioning import ensure_versions_synced, snapshot_versions

    from . import runs_bridge

    ticker = payload["ticker"]
    sector = payload.get("sector", "")
    side = payload["side"]  # "long" | "short" | "sector"
    borrow_veto = payload.get("borrow_veto", False)
    as_of = _resolve_as_of(payload["as_of_date"])
    personas = payload.get("personas") or None
    flavor = payload.get("flavor", "")
    run_id = payload.get("run_id")

    # Claim the queued Run row, if one was created for this candidate.
    run: Run | None = None
    selected_personas: list[str] = list(personas or ALL_PERSONAS)
    if run_id:
        try:
            run = Run.objects.get(pk=run_id)
        except Run.DoesNotExist:
            run = None
    if run is not None:
        if run.status == Run.CANCELLED:
            # User cancelled before this candidate started: emit a benign
            # hold so the chord can still finalize.
            return {
                "ticker": ticker, "sector": sector, "side": side,
                "borrow_veto": borrow_veto, "run_id": run_id,
                "decision": {
                    "ticker": ticker, "action": "hold",
                    "rationale": "cancelled before council started",
                },
                "risk": {}, "sector_veto_entry": None,
            }
        ensure_versions_synced()
        run.status = Run.RUNNING
        run.celery_task_id = str(getattr(self.request, "id", "") or "")
        run.agent_versions = snapshot_versions(selected_personas + [
            "fundamentals", "technicals", "valuation", "sentiment",
            "macro", "news_digest",
            "risk_manager", "portfolio_manager", "cio",
        ])
        run.save(update_fields=["status", "celery_task_id", "agent_versions"])

    if flavor == "sector_rotation":
        graph = build_sector_council_graph(personas=personas)
    else:
        graph = build_council_graph(personas=personas)
    council_user_id = payload.get("user_id")
    initial_state: dict = {
        "ticker": ticker,
        "as_of_date": as_of,
        "model_overrides": payload.get("model_overrides", {}),
        "data_provider": get_fmp_provider(user=council_user_id),
        "filings_provider": get_edgar_provider(),
        "ownership_provider": get_ownership_provider(user=council_user_id),
        # Cost attribution: every LLMCall this council emits will be linked
        # to BOTH the parent PortfolioTarget AND this run, so two independent
        # rollups stay correct (Run.total_cost_usd for the transcript view,
        # PortfolioTarget.total_cost_usd for the cycle view).
        "portfolio_target_id": payload.get("portfolio_target_id"),
        "run_id": run_id,
        "user_id": payload.get("user_id"),
        # P2e: tell PM/RM this is a short candidate so the action mapping
        # produces open_short instead of just sell, and so the borrow veto
        # propagates. Looser thresholds because the screener already
        # pre-filtered for attractiveness — we want the council to confirm,
        # not re-justify from zero.
        "pm_config": {
            "short_side": (side == "short"),
            "buy_threshold": 0.10,
            "sell_threshold": -0.10,
            "flavor": flavor,
            "bearish_veto_threshold": float(payload.get("bearish_veto_threshold", 0.70)),
        },
        "flavor": flavor,
        "sector": sector,
        "theme": payload.get("theme", ""),
        "borrow_veto": borrow_veto,
        "disable_cio": True,
    }
    failed = False
    try:
        final = graph.invoke(initial_state)
        decision = final.get("decision") or {}
        risk = final.get("risk") or {}
        sector_veto_entry = final.get("sector_veto_entry")
    except Exception as exc:  # pragma: no cover
        log.exception("council failed for %s", ticker)
        decision = {"ticker": ticker, "action": "hold", "rationale": f"council error: {exc}"}
        risk = {}
        sector_veto_entry = None
        final = {}
        failed = True

    if run is not None:
        # Persist transcript rows even on failure so the Run detail page
        # shows what we have. update_or_create makes this idempotent under
        # Celery retries.
        try:
            runs_bridge.persist_council_outputs(
                run=run, state=final, selected_personas=selected_personas, side=side,
            )
            runs_bridge.reconcile_run_cost(run)
        except Exception:  # pragma: no cover
            log.exception("persisting council outputs failed for run %s", run.pk)
        # Re-read status: the user may have cancelled mid-flight via the
        # /reject/ endpoint. Don't clobber a cancelled run with done/failed.
        current = Run.objects.filter(pk=run.pk).values_list("status", flat=True).first()
        if current != Run.CANCELLED:
            runs_bridge.mark_run(
                run,
                Run.FAILED if failed else Run.DONE,
                error_message=(decision.get("rationale", "") if failed else ""),
            )

    return {
        "ticker": ticker,
        "sector": sector,
        "side": side,
        "borrow_veto": borrow_veto,
        "run_id": run_id,
        "decision": decision,
        "risk": risk,
        "sector_veto_entry": sector_veto_entry,
    }


@shared_task
def finalize_cycle(council_results: list[dict], target_id: int) -> dict:
    target = PortfolioTarget.objects.select_related("strategy", "strategy__portfolio").get(
        pk=target_id
    )
    strategy = target.strategy
    portfolio = strategy.portfolio
    as_of = target.as_of_date

    # P2l: a target cancelled mid-fan-out should not be revived by the
    # callback writing target weights. Persist the council_results for audit
    # then return — the user's reject already marked candidate runs cancelled.
    if target.status == PortfolioTarget.CANCELLED:
        return {"target_id": target.pk, "status": "cancelled"}

    # Move into constructing state so the UI can distinguish "council still
    # running" from "constructor running" while we crunch numbers.
    PortfolioTarget.objects.filter(pk=target.pk).update(status=PortfolioTarget.CONSTRUCTING)
    target.status = PortfolioTarget.CONSTRUCTING

    # Build Constructor input.
    cands = []
    for r in council_results:
        decision = r.get("decision") or {}
        risk = r.get("risk") or {}
        veto_reason = None
        if r.get("borrow_veto"):
            veto_reason = "borrow_not_locatable"
        elif risk.get("veto"):
            veto_reason = "risk_veto"
        cands.append(Candidate(
            ticker=r["ticker"],
            sector=r.get("sector", ""),
            side=r["side"],
            action=str(decision.get("action", "hold")),
            confidence=int(decision.get("aggregate_confidence", 0)),
            quality_weight=1.0,
            veto_reason=veto_reason,
        ))

    constraints = Constraints(
        target_gross_pct=float(strategy.target_gross_pct),
        target_net_pct=float(strategy.target_net_pct),
        max_position_pct=float(strategy.max_position_pct),
        max_sector_pct=float(strategy.max_sector_pct),
        min_position_pct=float(strategy.min_position_pct),
    )

    # P2m: optional Markov regime exposure scaler. Off by default. When on,
    # constraints' target_net_pct (or target_gross_pct) is multiplied by a
    # clipped function of the configured ticker's bull_prob_1d - bear_prob_1d.
    from .regime_scaling import apply_regime_scaler
    constraints, regime_scaler_audit = apply_regime_scaler(
        strategy, constraints, as_of=as_of
    )

    portfolio_beta = 0.0
    beta_diagnostics: dict = {"regime_scaler": regime_scaler_audit.to_dict()}
    cycle_outcome = "target_created"
    per_position_thesis: dict[str, dict] = {}
    # P3b council-alpha: maps the council-free baseline constructor needs,
    # captured from the live flavor branch below so the baseline's inputs
    # match the live constructor's exactly.
    baseline_betas: dict[str, float] = {}
    baseline_asset_class_of: dict[str, str] = {}
    baseline_inverse_of: dict[str, str] = {}
    if strategy.kind == PortfolioStrategy.KIND_GLOBAL_MACRO:
        from apps.data.models import MacroSnapshot

        from .models import MacroETF, MacroRegimeSnapshot
        etf_rows = {e.ticker: e for e in MacroETF.objects.filter(is_active=True)}
        asset_class_of = {t: e.asset_class for t, e in etf_rows.items()}
        inverse_of = {t: e.inverse_of for t, e in etf_rows.items() if e.inverse_of}
        baseline_asset_class_of = asset_class_of
        baseline_inverse_of = inverse_of
        result = construct_global_macro(
            cands,
            target_gross_pct=float(strategy.target_gross_pct),
            per_etf_max_pct=float(strategy.per_etf_max_pct),
            per_etf_min_pct=float(strategy.per_etf_min_pct),
            max_etfs_held=int(strategy.max_etfs_held),
            asset_class_caps=strategy.asset_class_caps or None,
            asset_class_of=asset_class_of,
            inverse_of=inverse_of,
        )
        cycle_outcome = "target_created" if result.target_weights else "held_existing_book"
        for r in council_results:
            t = r["ticker"]
            if t in result.target_weights:
                decision = r.get("decision") or {}
                rationale = str(decision.get("rationale") or "").strip()
                per_position_thesis[t] = {
                    "ticker": t,
                    "sector": asset_class_of.get(t, r.get("sector", "")),
                    "action": decision.get("action", ""),
                    "aggregate_confidence": int(decision.get("aggregate_confidence", 0)),
                    "thesis": rationale[:2000],
                }
        # Snapshot the regime that drove this cycle (audit trail).
        snap = (
            MacroSnapshot.objects.filter(as_of_date__lte=as_of).order_by("-as_of_date").first()
        )
        from hedgefund_agents.screener.sector_features import macro_regime_vector
        regime_vec = macro_regime_vector(snap)
        MacroRegimeSnapshot.objects.update_or_create(
            strategy=strategy, as_of_date=as_of,
            defaults={
                "growth_score": (
                    regime_vec.get("early_cycle", 0.0)
                    + regime_vec.get("mid_cycle", 0.0)
                    - regime_vec.get("recession", 0.0)
                ),
                "inflation_score": regime_vec.get("sticky_inflation", 0.0),
                "policy_stance": (snap.policy_stance if snap else "neutral"),
                "yield_curve_state": (snap.yield_curve_state if snap else "flat"),
                "risk_on_score": (
                    regime_vec.get("early_cycle", 0.0)
                    - regime_vec.get("recession", 0.0)
                ),
                "regime_vector": regime_vec,
                "source_macro_snapshot_id": (snap.id if snap else None),
                "raw": {
                    "growth_quadrant": (snap.growth_quadrant if snap else ""),
                    "inflation_regime": (snap.inflation_regime if snap else ""),
                },
            },
        )
        # P02i review: enforce inverse-ETF holding-age policy. Find any
        # currently-held inverse ETF and check days_held vs the configured
        # max. Emit a diagnostic; the cycle reduces the target weight to 0
        # (forcing a close on the next pass) if the cap is exceeded.
        inverse_holdings_status: list[dict] = []
        if inverse_of:
            today = as_of
            held = (
                Position.objects.filter(portfolio=portfolio)
                .values_list("ticker", "opened_at")
            )
            held_inverse = {t: opened for t, opened in held if t in inverse_of}
            for t, opened in held_inverse.items():
                opened_date = opened.date() if hasattr(opened, "date") else opened
                days_held = (today - opened_date).days
                exceeded = days_held > int(strategy.max_inverse_etf_hold_days)
                inverse_holdings_status.append({
                    "ticker": t,
                    "days_held": int(days_held),
                    "max_days": int(strategy.max_inverse_etf_hold_days),
                    "exceeded": exceeded,
                    "tracking_partner": inverse_of.get(t, ""),
                })
                if exceeded and t in result.target_weights:
                    # Force-close: zero out the inverse position. The
                    # next cycle rebuilds exposure cleanly.
                    result.target_weights[t] = 0.0
        beta_diagnostics = {
            "netted_pairs": result.netted_pairs,
            "asset_class_exposure": result.asset_class_exposure,
            "regime_vector": regime_vec,
            "regime_scaler": regime_scaler_audit.to_dict(),
            "macro_regime_snapshot_id": (snap.id if snap else None),
            "inverse_holdings": inverse_holdings_status,
            "prefer_inverse_etf_over_short": bool(strategy.prefer_inverse_etf_over_short),
            "max_inverse_etf_hold_days": int(strategy.max_inverse_etf_hold_days),
        }
    elif strategy.kind == PortfolioStrategy.KIND_SECTOR_ROTATION:
        result = construct_sector_rotation(
            cands,
            target_gross_pct=float(strategy.target_gross_pct),
            per_etf_max_pct=float(strategy.per_etf_max_pct),
            per_etf_min_pct=float(strategy.per_etf_min_pct),
            max_etfs_held=int(strategy.max_etfs_held),
        )
        cycle_outcome = "target_created" if result.target_weights else "held_existing_book"
        for r in council_results:
            t = r["ticker"]
            if t in result.target_weights:
                decision = r.get("decision") or {}
                rationale = str(decision.get("rationale") or "").strip()
                per_position_thesis[t] = {
                    "ticker": t,
                    "sector": r.get("sector", ""),
                    "action": decision.get("action", ""),
                    "aggregate_confidence": int(decision.get("aggregate_confidence", 0)),
                    "thesis": rationale[:2000],
                }
        beta_diagnostics = {
            "overlap_dropped": result.overlap_dropped,
            "regime_scaler": regime_scaler_audit.to_dict(),
        }
    elif strategy.kind == PortfolioStrategy.KIND_CONCENTRATED_LONG:
        # PM action whitelist + confidence-gated construction. Refuses to over-
        # diversify when fewer than min_positions clear the bar.
        result = construct_concentrated_long(
            cands,
            constraints,
            min_positions=int(strategy.min_positions),
            max_positions=int(strategy.max_positions),
            min_aggregate_confidence=float(strategy.min_aggregate_confidence),
        )
        cycle_outcome = result.outcome
        # Capture thesis excerpts for the survivors only (the ones with weight).
        # P02g review: include the source Run id (the council transcript) and
        # the supporting confidence inputs so the UI can deep-link the thesis
        # back to its evidence trail.
        for r in council_results:
            t = r["ticker"]
            if t in result.target_weights:
                decision = r.get("decision") or {}
                rationale = str(decision.get("rationale") or "").strip()
                per_position_thesis[t] = {
                    "ticker": t,
                    "sector": r.get("sector", ""),
                    "action": decision.get("action", ""),
                    "aggregate_confidence": int(decision.get("aggregate_confidence", 0)),
                    "thesis": rationale[:2000],
                    "dissenting_personas": decision.get("dissenting_personas", []),
                    # P02g review source/evidence references.
                    "source_run_id": r.get("run_id"),
                    "source_decision": {
                        "rationale": rationale[:2000],
                        "aggregate_confidence": int(
                            decision.get("aggregate_confidence", 0)
                        ),
                        "target_weight_pct": decision.get("target_weight_pct"),
                        "target_quantity": decision.get("target_quantity"),
                    },
                }
    elif strategy.kind == PortfolioStrategy.KIND_MARKET_NEUTRAL:
        data_provider = get_fmp_provider(user=strategy.user)
        survivors = [c.ticker for c in cands if c.veto_reason is None]
        # P02f review: compute betas for current positions and a ranked
        # candidate buffer too, not only survivors. This ensures the UI can
        # show beta for held positions (even if they were dropped from this
        # cycle's survivor set), and supports replacement / partial-breach
        # diagnostics against the broader candidate pool.
        held_tickers = list(
            Position.objects.filter(portfolio=portfolio).values_list("ticker", flat=True)
        )
        all_cand_tickers = [c.ticker for c in cands]
        coverage_set = sorted(set(survivors + held_tickers + all_cand_tickers))
        betas_full = compute_betas_for(
            coverage_set,
            benchmark=strategy.benchmark_ticker,
            as_of=as_of,
            window_days=int(strategy.beta_window_days),
            data_provider=data_provider,
        )
        beta_map: dict[str, float] = {}
        beta_held: dict[str, float] = {}
        unreliable: list[str] = []
        for t, br in betas_full.items():
            if not br.reliable:
                unreliable.append(t)
                if strategy.drop_on_unreliable_beta:
                    for c in cands:
                        if c.ticker == t and c.veto_reason is None:
                            c.veto_reason = "beta_unreliable"
                    continue
                beta_map[t] = 1.0
            else:
                beta_map[t] = br.beta
            if t in held_tickers:
                beta_held[t] = beta_map.get(t, 1.0)

        baseline_betas = beta_map
        neutral = construct_market_neutral(
            cands, constraints, beta_map,
            tol_dollar=float(strategy.neutrality_tolerance_dollar_pct),
            tol_beta=float(strategy.neutrality_tolerance_beta),
        )
        # P02f review: persist clear breach severity diagnostics in addition
        # to the existing alpha_clamped flag.
        breach_severity = ""
        if neutral.diagnostics.get("alpha_clamped"):
            beta_drift = abs(neutral.portfolio_beta)
            tol = float(strategy.neutrality_tolerance_beta)
            if beta_drift > tol * 4:
                breach_severity = "severe"
            elif beta_drift > tol * 2:
                breach_severity = "moderate"
            else:
                breach_severity = "mild"
        beta_diagnostics = {
            **neutral.diagnostics,
            "unreliable": unreliable,
            "benchmark": strategy.benchmark_ticker,
            "window_days": int(strategy.beta_window_days),
            "n_betas": len(beta_map),
            "n_held_with_beta": len(beta_held),
            "n_coverage_set": len(coverage_set),
            "beta_held": beta_held,
            "breach_severity": breach_severity,
            "regime_scaler": regime_scaler_audit.to_dict(),
        }
        portfolio_beta = neutral.portfolio_beta
        result = neutral
    else:
        result = construct(cands, constraints)

    # P3b council-alpha: compute the council-free deterministic baseline book
    # for this cycle (same constructor + scaled constraints, screener score in
    # place of council confidence, no veto). A baseline failure must never
    # break the live cycle, so it's best-effort.
    baseline_weights: dict[str, float] = {}
    baseline_version = ""
    if target.screener_ranking_id:
        try:
            from apps.leaderboard.council_alpha import (
                BASELINE_VERSION,
                baseline_target_weights,
            )
            baseline_weights = baseline_target_weights(
                strategy, target.screener_ranking, constraints,
                betas=baseline_betas,
                asset_class_of=baseline_asset_class_of,
                inverse_of=baseline_inverse_of,
            )
            baseline_version = BASELINE_VERSION
        except Exception:
            log.exception("council-alpha baseline failed for target %s", target.pk)

    # Pull last close per ticker for the rebalancer.
    data_provider = get_fmp_provider(user=strategy.user)
    last_close: dict[str, float] = {}
    existing_tickers = list(
        Position.objects.filter(portfolio=portfolio).values_list("ticker", flat=True)
    )
    for t in set(list(result.target_weights.keys()) + existing_tickers):
        try:
            bars = data_provider.get_daily_bars(
                t, start=as_of, end=as_of, as_of=as_of
            ) or data_provider.get_daily_bars(
                t, start=as_of.replace(day=1), end=as_of, as_of=as_of
            )
            if bars:
                last_close[t] = float(bars[-1].close)
        except Exception:
            last_close[t] = 0.0

    current = [
        CurrentPosition(
            ticker=p.ticker,
            quantity=float(p.quantity),
            avg_cost=float(p.avg_cost),
            sector=p.sector,
        )
        for p in Position.objects.filter(portfolio=portfolio)
    ]
    portfolio_value = float(portfolio.cash_balance) + sum(
        last_close.get(p.ticker, float(p.avg_cost)) * float(p.quantity) for p in
        Position.objects.filter(portfolio=portfolio)
    )
    if cycle_outcome == "held_existing_book":
        # No-action cycle: do not produce orders. The existing book is held.
        orders = []
    else:
        orders = compute_orders(
            current,
            result.target_weights,
            RebalanceConfig(
                portfolio_value=max(1.0, portfolio_value),
                last_close=last_close,
                min_trade_notional_usd=float(strategy.min_trade_notional_usd),
                max_turnover_pct=float(strategy.max_turnover_pct),
            ),
        )

    RebalanceOrder.objects.filter(target=target).delete()
    RebalanceOrder.objects.bulk_create([
        RebalanceOrder(
            target=target, ticker=o.ticker, side=o.side,
            quantity=Decimal(str(round(o.quantity, 6))),
            limit_price=Decimal(str(round(o.limit_price, 4))) if o.limit_price else None,
            reason=o.reason,
            estimated_notional_usd=Decimal(str(round(o.estimated_notional_usd, 2))),
            sequence=o.sequence,
        ) for o in orders
    ])

    target.target_weights = {t: round(w, 6) for t, w in result.target_weights.items()}
    target.baseline_weights = baseline_weights
    target.baseline_version = baseline_version
    target.gross_pct = Decimal(str(round(result.gross_pct, 4)))
    target.net_pct = Decimal(str(round(result.net_pct, 4)))
    target.realised_net_pct = Decimal(str(round(result.net_pct, 4)))
    target.realised_portfolio_beta = Decimal(str(round(portfolio_beta, 3)))
    # P02e review: merge construction diagnostics (requested vs achieved
    # gross/net, binding caps, underinvestment reason) into beta_diagnostics
    # so the strategy detail UI can render the feasibility summary.
    if getattr(result, "diagnostics", None):
        beta_diagnostics = {**beta_diagnostics, "construction": result.diagnostics}
    target.beta_diagnostics = beta_diagnostics
    target.per_position_thesis = per_position_thesis
    target.cycle_outcome = cycle_outcome
    target.sector_exposure = {s: round(w, 6) for s, w in result.sector_exposure.items()}
    target.rejected_candidates = result.rejected
    target.decisions = council_results
    # Persist sector council diagnostics whenever the sector graph ran —
    # i.e. for any candidate whose council emitted a sector_veto_entry,
    # not just kind=sector_rotation (L/S on ETFs auto-routes through it too).
    sector_entries = [
        r["sector_veto_entry"] for r in council_results if r.get("sector_veto_entry")
    ]
    if sector_entries:
        target.sector_veto_log = sector_entries
    target.status = "done"
    target.finished_at = timezone.now()
    target.save()
    _maybe_auto_enroll(target)
    # P7: shared terminal hook — for a broker-linked strategy with an enabled
    # autopilot, autonomously convert this cycle into paper broker orders (the
    # status flips to autopilot_submitted inside). No-op otherwise.
    from .autopilot import _finalize_target

    _finalize_target(target)

    PortfolioStrategy.objects.filter(pk=strategy.pk).update(last_run_at=timezone.now())
    return {"target_id": target.pk, "orders": len(orders), "status": "done"}


def _run_risk_parity_cycle(
    strategy: PortfolioStrategy, as_of: date_cls, members: list[tuple[str, str]],
    *, supersedes_target_id: int | None = None,
) -> dict:
    """Deterministic inverse-vol cycle. No LLM, no screener."""
    data_provider = get_fmp_provider(user=strategy.user)
    tickers = [t for t, _ in members]
    vols_map = compute_vols_for(
        tickers, as_of,
        window_days=int(strategy.vol_window_days),
        data_provider=data_provider,
    )
    vols = {t: v.daily_vol for t, v in vols_map.items()}

    portfolio = strategy.portfolio
    portfolio_value_pre = float(portfolio.cash_balance) + sum(
        float(p.avg_cost) * float(p.quantity)
        for p in Position.objects.filter(portfolio=portfolio)
    )

    # Current weights for the band check.
    current_weights: dict[str, float] = {}
    if portfolio_value_pre > 0:
        for p in Position.objects.filter(portfolio=portfolio):
            current_weights[p.ticker] = (
                float(p.avg_cost) * float(p.quantity) / portfolio_value_pre
            )

    # P2m: optional Markov regime gate excludes sleeves whose 5-day bear
    # probability exceeds the configured threshold. Off by default.
    from .regime_scaling import regime_gate_excluded_sleeves
    markov_excluded = regime_gate_excluded_sleeves(strategy, members, as_of=as_of)

    result = construct_risk_parity(
        members,
        vols,
        target_gross_pct=float(strategy.target_gross_pct or 1.0),
        per_sleeve_max_pct=float(strategy.per_etf_max_pct or 0.50),
        per_sleeve_min_pct=float(strategy.per_etf_min_pct or 0.02),
        excluded=markov_excluded or None,
        current_weights=current_weights or None,
        rebalance_band_pct=float(strategy.rebalance_band_pct),
    )
    # P02j review: optional council-veto branch. Deterministic by default
    # (enable_council_veto=False). When enabled, the council may trim
    # individual sleeve weights down by up to ``veto_max_trim`` (here 50%
    # of the sleeve's deterministic weight). Trimmed amounts are
    # redistributed across surviving sleeves so the gross stays at the
    # target. Veto reasons are persisted; the baseline (pre-veto) weights
    # are kept in diagnostics["deterministic_weights"] so backtests can
    # compute the council-alpha vs the baseline.
    council_veto_log: list[dict] = []
    if bool(strategy.enable_council_veto) and result.target_weights:
        # In this synchronous path we don't actually invoke the council
        # (would require Celery chord + cost-bounded fan-out). Instead, we
        # respect any ``markov_excluded`` flags already collected and
        # build a deterministic veto log so the contract is exercised.
        # The full LLM-veto path is a follow-on (see PROGRESS.md).
        if markov_excluded:
            for ticker, reason in markov_excluded.items():
                if ticker in result.target_weights:
                    pre = result.target_weights[ticker]
                    result.target_weights[ticker] = 0.0
                    council_veto_log.append({
                        "ticker": ticker,
                        "action": "veto",
                        "reason": reason,
                        "pre_weight": round(pre, 6),
                        "post_weight": 0.0,
                    })
            # Redistribute zeroed weight across survivors.
            survivors_w = {t: w for t, w in result.target_weights.items() if w > 0}
            survivors_total = sum(survivors_w.values())
            target_gross = float(strategy.target_gross_pct or 1.0)
            if survivors_total > 0:
                scale = target_gross / survivors_total
                for t in survivors_w:
                    result.target_weights[t] = round(
                        result.target_weights[t] * scale, 6,
                    )

    # P7c Part D — leverage (scale the inverse-vol book toward rp_vol_target_annual
    # up to rp_max_gross) then the SPY-200dMA regime gate (de-gross in risk-off).
    # Applied IDENTICALLY in the construct_risk_parity backtest mode (engine.py) so
    # the levered/gated book stays deploy-faithful. Both off by default.
    rp_vt = float(strategy.rp_vol_target_annual or 0)
    if rp_vt > 0 and result.target_weights:
        mg = float(strategy.rp_max_gross or 1.0)
        port_vol = sum(
            result.target_weights[t] * vols.get(t, 0.0) * (252 ** 0.5)
            for t in result.target_weights
        )
        if port_vol > 0:
            lev = min(mg, rp_vt / port_vol)
            result.target_weights = {t: w * lev for t, w in result.target_weights.items()}
    if bool(strategy.enable_spy_regime_gate) and result.target_weights:
        from apps.backtests.engine import spy_regime_scale
        rs = spy_regime_scale(as_of, floor=float(strategy.regime_gate_floor))
        if rs != 1.0:
            result.target_weights = {t: w * rs for t, w in result.target_weights.items()}
    if rp_vt > 0 or bool(strategy.enable_spy_regime_gate):
        result.gross_pct = sum(abs(w) for w in result.target_weights.values())
        result.net_pct = sum(result.target_weights.values())
        # within_band was computed on the UNLEVERED/ungated drift; the levered or
        # gated target differs materially, so force a rebalance (the backtest has no
        # band) — else the band could keep skipping and the lever never applies.
        result.within_band = False

    with transaction.atomic():
        target = _resolve_cycle_target(
            strategy, as_of,
            supersedes_target_id=supersedes_target_id,
            defaults={
                "status": "running",
                "target_weights": {},
                "rejected_candidates": [],
                "decisions": [],
                "error_message": "",
                "finished_at": None,
            },
        )

    last_close: dict[str, float] = {}
    existing_tickers = list(
        Position.objects.filter(portfolio=portfolio).values_list("ticker", flat=True)
    )
    for t in set(list(result.target_weights.keys()) + existing_tickers):
        try:
            bars = data_provider.get_daily_bars(
                t, start=as_of, end=as_of, as_of=as_of
            ) or data_provider.get_daily_bars(
                t, start=as_of.replace(day=1), end=as_of, as_of=as_of
            )
            if bars:
                last_close[t] = float(bars[-1].close)
        except Exception:
            last_close[t] = 0.0

    current = [
        CurrentPosition(
            ticker=p.ticker, quantity=float(p.quantity),
            avg_cost=float(p.avg_cost), sector=p.sector,
        )
        for p in Position.objects.filter(portfolio=portfolio)
    ]
    portfolio_value = float(portfolio.cash_balance) + sum(
        last_close.get(p.ticker, float(p.avg_cost)) * float(p.quantity)
        for p in Position.objects.filter(portfolio=portfolio)
    )

    cycle_outcome = "target_created"
    if result.within_band:
        # In-band: emit the target so the UI shows ideal vs current, but skip
        # trading entirely (death-by-costs avoidance).
        cycle_outcome = "within_rebalance_band"
        orders = []
    else:
        orders = compute_orders(
            current,
            result.target_weights,
            RebalanceConfig(
                portfolio_value=max(1.0, portfolio_value),
                last_close=last_close,
                min_trade_notional_usd=float(strategy.min_trade_notional_usd),
                max_turnover_pct=float(strategy.max_turnover_pct),
            ),
        )

    RebalanceOrder.objects.filter(target=target).delete()
    RebalanceOrder.objects.bulk_create([
        RebalanceOrder(
            target=target, ticker=o.ticker, side=o.side,
            quantity=Decimal(str(round(o.quantity, 6))),
            limit_price=Decimal(str(round(o.limit_price, 4))) if o.limit_price else None,
            reason=o.reason,
            estimated_notional_usd=Decimal(str(round(o.estimated_notional_usd, 2))),
            sequence=o.sequence,
        ) for o in orders
    ])

    target.target_weights = {t: round(w, 6) for t, w in result.target_weights.items()}
    target.gross_pct = Decimal(str(round(result.gross_pct, 4)))
    target.net_pct = Decimal(str(round(result.net_pct, 4)))
    target.realised_net_pct = Decimal(str(round(result.net_pct, 4)))
    target.sector_exposure = {s: round(w, 6) for s, w in result.sector_exposure.items()}
    target.rejected_candidates = result.rejected
    target.beta_diagnostics = {
        **result.diagnostics,
        "within_band": result.within_band,
        "max_drift": round(result.max_drift, 4),
        "rebalance_band_pct": float(strategy.rebalance_band_pct),
        "markov_gate_enabled": bool(strategy.enable_markov_regime_gate),
        "markov_bear_prob_5d_threshold": float(strategy.markov_bear_prob_5d_threshold),
        "markov_excluded_sleeves": markov_excluded,
        # P02j review: surface the council-veto branch so the UI can show
        # whether vetoes happened and which sleeves were trimmed.
        "enable_council_veto": bool(strategy.enable_council_veto),
        "council_veto_log": council_veto_log,
    }
    # P3b council-alpha: risk-parity is council-free, so the baseline IS the
    # realised book ⇒ council-alpha is 0 (veto-alpha; nonzero only once
    # council-veto mode lands).
    from apps.leaderboard.council_alpha import BASELINE_VERSION
    target.baseline_weights = target.target_weights
    target.baseline_version = BASELINE_VERSION
    target.cycle_outcome = cycle_outcome
    target.decisions = []
    target.status = "done"
    target.finished_at = timezone.now()
    target.save()
    _maybe_auto_enroll(target)
    # P7b (ADR 0025 §1): the deterministic cycle reaches the SAME terminal hook
    # the council path uses (tasks.py:954) — for a broker-linked, enabled autopilot
    # this converts the inverse-vol target into paper broker orders. Called
    # UNCONDITIONALLY, even on a within_band cycle: the broker account is the book
    # of record, so the bridge recomputes the delta against the REAL broker book.
    # The strategy-book rebalance band is a strategy-portfolio optimization that
    # does not gate the broker book — and the deterministic backtest rebalances to
    # the full target each period with no band (engine.inverse_vol_weights passes no
    # current_weights), so rebalancing the broker book every cycle is what keeps
    # live ≡ backtest. No-op (byte-identical) for a non-autopilot run like #16.
    from .autopilot import _finalize_target

    _finalize_target(target)

    PortfolioStrategy.objects.filter(pk=strategy.pk).update(last_run_at=timezone.now())
    return {"target_id": target.pk, "orders": len(orders), "status": "done"}


def _run_momentum_cycle(
    strategy: PortfolioStrategy, as_of: date_cls, members: list[tuple[str, str]],
    *, sizing: str, supersedes_target_id: int | None = None,
) -> dict:
    """Deterministic momentum cycle (trend / sector). No LLM, no screener — sizes
    off the momentum signal the council kinds discard (P7c / ADR 0026). Mirrors
    `_run_risk_parity_cycle`'s persist/orders/bridge flow; the only differences are
    the constructor and the trailing-bar prefetch the engine sizer reads."""
    from datetime import timedelta

    from .construction import (
        construct_sector_momentum,
        construct_trend,
        sector_momentum_config,
        trend_config,
    )

    data_provider = get_fmp_provider(user=strategy.user)
    tickers = [t for t, _ in members]
    # The engine sizer reads trailing bars from DailyBar (date__lt=as_of). Prefetch
    # the lookback window so the live book sees the same history the backtest does
    # (get_daily_bars self-caches; as_of clamps it so the read stays point-in-time).
    lookback_start = as_of - timedelta(days=420)
    for t in tickers:
        try:
            data_provider.get_daily_bars(t, start=lookback_start, end=as_of, as_of=as_of)
        except Exception:
            continue

    portfolio = strategy.portfolio
    portfolio_value_pre = float(portfolio.cash_balance) + sum(
        float(p.avg_cost) * float(p.quantity)
        for p in Position.objects.filter(portfolio=portfolio)
    )
    current_weights: dict[str, float] = {}
    if portfolio_value_pre > 0:
        for p in Position.objects.filter(portfolio=portfolio):
            current_weights[p.ticker] = (
                float(p.avg_cost) * float(p.quantity) / portfolio_value_pre
            )

    if sizing == "tsmom":
        result = construct_trend(
            as_of, members, config=trend_config(strategy),
            current_weights=current_weights or None,
        )
    else:
        result = construct_sector_momentum(
            as_of, members, config=sector_momentum_config(strategy),
            current_weights=current_weights or None,
        )

    # P7c Part D — SPY-200dMA regime gate (de-gross in risk-off). Deploy-faithful
    # with run_deterministic_segment's gate. Off by default.
    if bool(strategy.enable_spy_regime_gate) and result.target_weights:
        from apps.backtests.engine import spy_regime_scale
        rs = spy_regime_scale(as_of, floor=float(strategy.regime_gate_floor))
        if rs != 1.0:
            result.target_weights = {t: w * rs for t, w in result.target_weights.items()}
            result.gross_pct = sum(abs(w) for w in result.target_weights.values())
            result.net_pct = sum(result.target_weights.values())

    with transaction.atomic():
        target = _resolve_cycle_target(
            strategy, as_of,
            supersedes_target_id=supersedes_target_id,
            defaults={
                "status": "running", "target_weights": {}, "rejected_candidates": [],
                "decisions": [], "error_message": "", "finished_at": None,
            },
        )

    last_close: dict[str, float] = {}
    existing_tickers = list(
        Position.objects.filter(portfolio=portfolio).values_list("ticker", flat=True)
    )
    for t in set(list(result.target_weights.keys()) + existing_tickers):
        try:
            bars = data_provider.get_daily_bars(
                t, start=as_of, end=as_of, as_of=as_of
            ) or data_provider.get_daily_bars(
                t, start=as_of.replace(day=1), end=as_of, as_of=as_of
            )
            if bars:
                last_close[t] = float(bars[-1].close)
        except Exception:
            last_close[t] = 0.0

    current = [
        CurrentPosition(
            ticker=p.ticker, quantity=float(p.quantity),
            avg_cost=float(p.avg_cost), sector=p.sector,
        )
        for p in Position.objects.filter(portfolio=portfolio)
    ]
    portfolio_value = float(portfolio.cash_balance) + sum(
        last_close.get(p.ticker, float(p.avg_cost)) * float(p.quantity)
        for p in Position.objects.filter(portfolio=portfolio)
    )

    # No rebalance band — momentum rebalances to the full target each cycle, so the
    # live book tracks the backtest (which rebalances every period with no band).
    orders = compute_orders(
        current, result.target_weights,
        RebalanceConfig(
            portfolio_value=max(1.0, portfolio_value),
            last_close=last_close,
            min_trade_notional_usd=float(strategy.min_trade_notional_usd),
            max_turnover_pct=float(strategy.max_turnover_pct),
        ),
    )

    RebalanceOrder.objects.filter(target=target).delete()
    RebalanceOrder.objects.bulk_create([
        RebalanceOrder(
            target=target, ticker=o.ticker, side=o.side,
            quantity=Decimal(str(round(o.quantity, 6))),
            limit_price=Decimal(str(round(o.limit_price, 4))) if o.limit_price else None,
            reason=o.reason,
            estimated_notional_usd=Decimal(str(round(o.estimated_notional_usd, 2))),
            sequence=o.sequence,
        ) for o in orders
    ])

    target.target_weights = {t: round(w, 6) for t, w in result.target_weights.items()}
    target.gross_pct = Decimal(str(round(result.gross_pct, 4)))
    target.net_pct = Decimal(str(round(result.net_pct, 4)))
    target.realised_net_pct = Decimal(str(round(result.net_pct, 4)))
    target.sector_exposure = {s: round(w, 6) for s, w in result.sector_exposure.items()}
    target.rejected_candidates = result.rejected
    target.beta_diagnostics = {**result.diagnostics, "sizing": sizing}
    from apps.leaderboard.council_alpha import BASELINE_VERSION
    target.baseline_weights = target.target_weights
    target.baseline_version = BASELINE_VERSION
    target.cycle_outcome = "target_created"
    target.decisions = []
    target.status = "done"
    target.finished_at = timezone.now()
    target.save()
    _maybe_auto_enroll(target)
    # Same ADR-0025 terminal hook as risk-parity → converts the target into paper
    # broker orders for a linked, enabled autopilot (deterministic kinds bypass the
    # bridge's vol-target/equity-cap steps; binding sizing already done here).
    from .autopilot import _finalize_target

    _finalize_target(target)
    PortfolioStrategy.objects.filter(pk=strategy.pk).update(last_run_at=timezone.now())
    return {"target_id": target.pk, "orders": len(orders), "status": "done"}


def _run_news_sentiment_cycle(
    strategy: PortfolioStrategy, as_of: date_cls, members: list[tuple[str, str]],
    *, supersedes_target_id: int | None = None,
) -> dict:
    """Long-only single-name cycle sized off NEWS SENTIMENT (P7c E4), with an
    optional bounded persona conviction overlay (``enable_council_veto``). The
    council in a LANGUAGE role, not the sizer; ``council_alpha`` measures the
    overlay vs the no-overlay baseline forward (news can't be backtested)."""
    from apps.leaderboard.council_alpha import BASELINE_VERSION

    from .news_sentiment import (
        NEWS_CONVICTION_MODEL,
        construct_news_sentiment,
        news_conviction,
        news_sentiment_scores,
    )

    data_provider = get_fmp_provider(user=strategy.user)
    tickers = [t for t, _ in members]
    sectors = {t: sec for t, sec in members}
    top_n = int(strategy.max_positions or 15)
    target_gross = float(strategy.target_gross_pct or 1.0)

    scores = news_sentiment_scores(tickers, as_of)

    # P10 §E2: resolve the cycle target FIRST so the conviction overlay's LLM
    # calls can be metered against it (record_llm_call → council_cost_usd).
    portfolio = strategy.portfolio
    with transaction.atomic():
        target = _resolve_cycle_target(
            strategy, as_of,
            supersedes_target_id=supersedes_target_id,
            defaults={
                "status": "running", "target_weights": {}, "rejected_candidates": [],
                "decisions": [], "error_message": "", "finished_at": None,
            },
        )

    # Bounded persona conviction overlay (opt-in). Over-select 2x so vetoes don't
    # under-deploy; the constructor re-ranks by sentiment x conviction.
    conviction: dict[str, float] | None = None
    candidates: list[str] = []
    if bool(strategy.enable_council_veto):
        candidates = sorted(
            (t for t, s in scores.items() if s > 0), key=lambda t: scores[t], reverse=True
        )[: top_n * 2]
        if candidates:
            conviction = news_conviction(
                candidates, as_of, model=NEWS_CONVICTION_MODEL,
                user_id=strategy.user_id, portfolio_target_id=target.pk,
            )

    result = construct_news_sentiment(
        scores, sectors, top_n=top_n, conviction=conviction, target_gross=target_gross
    )
    # council_alpha baseline: the SAME signal with NO overlay (the council-free book).
    baseline = construct_news_sentiment(
        scores, sectors, top_n=top_n, conviction=None, target_gross=target_gross
    )
    # P10 §E5: a SECOND, sentiment-free baseline — the same universe equal-
    # weighted. council_alpha alone only measures the overlay increment; this
    # book lets the nightly scorer test whether LLM sentiment itself beats a
    # $0 deterministic basket (the kill-the-sleeve criterion, ADR-0027).
    ew_weights = (
        {t: round(target_gross / len(tickers), 6) for t in tickers} if tickers else {}
    )

    last_close: dict[str, float] = {}
    existing = list(Position.objects.filter(portfolio=portfolio).values_list("ticker", flat=True))
    for t in set(list(result.target_weights.keys()) + existing):
        try:
            bars = data_provider.get_daily_bars(
                t, start=as_of.replace(day=1), end=as_of, as_of=as_of
            )
            if bars:
                last_close[t] = float(bars[-1].close)
        except Exception:
            last_close[t] = 0.0

    current = [
        CurrentPosition(
            ticker=p.ticker, quantity=float(p.quantity),
            avg_cost=float(p.avg_cost), sector=p.sector,
        )
        for p in Position.objects.filter(portfolio=portfolio)
    ]
    portfolio_value = float(portfolio.cash_balance) + sum(
        last_close.get(p.ticker, float(p.avg_cost)) * float(p.quantity)
        for p in Position.objects.filter(portfolio=portfolio)
    )
    orders = compute_orders(
        current, result.target_weights,
        RebalanceConfig(
            portfolio_value=max(1.0, portfolio_value),
            last_close=last_close,
            min_trade_notional_usd=float(strategy.min_trade_notional_usd),
            max_turnover_pct=float(strategy.max_turnover_pct),
        ),
    )

    RebalanceOrder.objects.filter(target=target).delete()
    RebalanceOrder.objects.bulk_create([
        RebalanceOrder(
            target=target, ticker=o.ticker, side=o.side,
            quantity=Decimal(str(round(o.quantity, 6))),
            limit_price=Decimal(str(round(o.limit_price, 4))) if o.limit_price else None,
            reason=o.reason,
            estimated_notional_usd=Decimal(str(round(o.estimated_notional_usd, 2))),
            sequence=o.sequence,
        ) for o in orders
    ])

    target.target_weights = {t: round(w, 6) for t, w in result.target_weights.items()}
    target.gross_pct = Decimal(str(round(result.gross_pct, 4)))
    target.net_pct = Decimal(str(round(result.net_pct, 4)))
    target.realised_net_pct = Decimal(str(round(result.net_pct, 4)))
    target.sector_exposure = {s: round(w, 6) for s, w in result.sector_exposure.items()}
    target.rejected_candidates = result.rejected
    target.beta_diagnostics = {
        **result.diagnostics,
        "overlay": "council" if conviction else "none",
        "vetoed": sorted(t for t, c in (conviction or {}).items() if c == 0.0),
        # P10 §E4: persist every per-candidate decision so the nightly scorer
        # can grade each conviction/veto against the name's forward return vs
        # the basket (book-level weekly diffs need years for significance;
        # ~30 name-level decisions/cycle reach power in ~26-30 weeks).
        "conviction": {t: round(c, 4) for t, c in (conviction or {}).items()},
        "candidates": candidates,
        "candidate_sentiment": {
            t: round(scores[t], 4) for t in candidates if t in scores
        },
        # P10 §E5: the sentiment-free equal-weight book of the same universe.
        "ew_baseline_weights": ew_weights,
    }
    # council_alpha (E3 harness): baseline = the same news signal with NO persona
    # overlay → the nightly leaderboard differences realised − baseline forward.
    target.baseline_weights = {t: round(w, 6) for t, w in baseline.target_weights.items()}
    target.baseline_version = BASELINE_VERSION
    target.cycle_outcome = "target_created"
    target.decisions = []
    target.status = "done"
    target.finished_at = timezone.now()
    target.save()
    _maybe_auto_enroll(target)
    from .autopilot import _finalize_target

    _finalize_target(target)
    PortfolioStrategy.objects.filter(pk=strategy.pk).update(last_run_at=timezone.now())
    return {
        "target_id": target.pk, "orders": len(orders), "status": "done",
        "overlay": bool(conviction),
    }


def _run_pairs_cycle(
    strategy: PortfolioStrategy, as_of: date_cls, members: list[tuple[str, str]],
    *, supersedes_target_id: int | None = None,
    override_preset: str | None = None,
    override_models: dict[str, str] | None = None,
) -> dict:
    """Deterministic pairs-trading cycle (no LLM, no council).

    Screen within-sector pairs by cointegration + correlation + |z| > entry.
    Close mean-reverted / stopped open pairs first; then fill remaining slots
    with top |z| new candidates. target_weights collapses paired legs into
    signed per-ticker weights (long_a positive, short_b = - hedge_ratio·notional).
    """
    data_provider = get_fmp_provider(user=strategy.user)
    lookback = int(strategy.pair_lookback_days)
    portfolio = strategy.portfolio

    open_pairs = list(Pair.objects.filter(strategy=strategy, status="open"))
    tickers_needed = {t for t, _ in members}
    for p in open_pairs:
        tickers_needed.add(p.leg_a_ticker)
        tickers_needed.add(p.leg_b_ticker)
    # Pull log-prices once for all tickers.
    bundle = gather_log_prices(
        list(tickers_needed), as_of, lookback_days=lookback, data_provider=data_provider
    )

    # 1) Evaluate open pairs for exit / stop / regime-break.
    exit_z = float(strategy.pair_exit_z)
    stop_z = float(strategy.pair_stop_z)
    p_max = float(strategy.pair_cointegration_p_max)
    closes_log: list[dict] = []
    held_pairs: list[Pair] = []
    for p in open_pairs:
        action, z, p_value = decide_open_pair_action(
            leg_a=p.leg_a_ticker, leg_b=p.leg_b_ticker,
            hedge_ratio=p.hedge_ratio,
            spread_mean=p.spread_mean, spread_std=p.spread_std,
            log_prices=bundle.log_prices,
            exit_z=exit_z, stop_z=stop_z, p_max=p_max,
            consecutive_failures=int(p.consecutive_coint_failures or 0),
        )
        if action == "no_price":
            p.status = "closed"
            p.exit_date = as_of
            p.exit_reason = "no_price"
            p.save()
            closes_log.append({"pair_id": p.pk, "reason": "no_price", "z": None,
                               "leg_a": p.leg_a_ticker, "leg_b": p.leg_b_ticker})
            continue
        if action in ("stopped", "reverted", "regime_break"):
            # Hedge-ratio drift: re-fit β on current prices and compare to entry β.
            drift_pct: float | None = None
            la = bundle.log_prices.get(p.leg_a_ticker) or []
            lb = bundle.log_prices.get(p.leg_b_ticker) or []
            n = min(len(la), len(lb))
            if n >= 60 and float(p.hedge_ratio) > 0:
                _alpha, beta_now = _ols_alpha_beta(la[-n:], lb[-n:])
                if beta_now > 0:
                    drift_pct = (beta_now / float(p.hedge_ratio)) - 1.0
            p.status = "closed"
            p.exit_z = z
            p.exit_date = as_of
            p.exit_reason = action
            p.hedge_ratio_drift_pct = drift_pct
            p.save()
            closes_log.append({
                "pair_id": p.pk, "reason": action, "z": round(z or 0.0, 3),
                "leg_a": p.leg_a_ticker, "leg_b": p.leg_b_ticker,
                "hedge_ratio_drift_pct": (round(drift_pct, 4) if drift_pct is not None else None),
            })
            continue
        # action == "hold": persist the consecutive-failure counter for next cycle.
        if p_value is not None and p_value > p_max:
            p.consecutive_coint_failures = int(p.consecutive_coint_failures or 0) + 1
        else:
            p.consecutive_coint_failures = 0
        p.save(update_fields=["consecutive_coint_failures"])
        held_pairs.append(p)

    # 2) Screen for new candidates.
    candidates, screener_diag = screen_pairs(
        members, as_of,
        data_provider=data_provider,
        lookback_days=lookback,
        p_max=float(strategy.pair_cointegration_p_max),
        corr_min=float(strategy.pair_correlation_min),
        entry_z=float(strategy.pair_entry_z),
    )
    # Drop candidates that duplicate an open pair (same legs in any order).
    open_keys = {tuple(sorted([p.leg_a_ticker, p.leg_b_ticker])) for p in held_pairs}
    candidates = [
        c for c in candidates
        if tuple(sorted([c.leg_a, c.leg_b])) not in open_keys
    ]

    available_slots = max(0, int(strategy.pair_max_held) - len(held_pairs))
    # Take a wider top-K when the council is on so we can survive skips.
    council_on = bool(getattr(strategy, "enable_pair_council", False))
    pool_size = max(available_slots * 3, available_slots) if council_on else available_slots
    top_pool = candidates[:pool_size]

    # P02k review: persist screened candidates as Pair rows with
    # status="candidate" before entry. This gives the auditor a full
    # history of what the screener proposed, not only what was promoted.
    # Promotion-to-open later in this function reuses these rows by
    # update_or_create on the (strategy, leg_a, leg_b) tuple.
    candidate_pair_rows: dict[tuple[str, str], Pair] = {}
    for c in top_pool:
        row, _ = Pair.objects.update_or_create(
            strategy=strategy,
            leg_a_ticker=c.leg_a, leg_b_ticker=c.leg_b,
            status="candidate",
            defaults={
                "sector": c.sector,
                "cointegration_p_value": c.p_value,
                "correlation": c.correlation,
                "hedge_ratio": c.hedge_ratio,
                "spread_mean": c.spread_mean,
                "spread_std": c.spread_std,
                "spread_window_days": lookback,
            },
        )
        candidate_pair_rows[(c.leg_a, c.leg_b)] = row

    # 3) Optional council: vet each candidate; drop skips + low-confidence enters.
    council_log: list[dict] = []
    accepted: list = []
    if council_on and top_pool:
        from decimal import Decimal as _Dec

        from apps.runs.models import AgentMessage as _AgentMessage
        from apps.runs.models import Decision as _Decision
        from apps.runs.models import Run as _Run
        from hedgefund_agents.pairs_council import debate_pair

        from .models import PortfolioTargetRun as _PortfolioTargetRun
        min_conf = float(strategy.pair_council_min_confidence)
        overrides = _resolve_model_overrides(
            strategy, preset=override_preset, overrides=override_models
        )
        personas = list(strategy.personas or [])
        news_service = get_news_service(user=strategy.user)
        # Ensure a target row exists so we can link Runs to it. The cycle's
        # target row is created later below via _resolve_cycle_target — for pair
        # councils we need it earlier so audit Runs have a parent. On a rerun
        # this creates the fresh superseding row; the later call reuses it.
        with transaction.atomic():
            audit_target = _resolve_cycle_target(
                strategy, as_of,
                supersedes_target_id=supersedes_target_id,
                defaults={
                    "status": PortfolioTarget.RUNNING_COUNCIL,
                    "target_weights": {},
                    "rejected_candidates": [],
                    "decisions": [],
                    "error_message": "",
                    "finished_at": None,
                },
            )
        # Cache per-ticker headline lists across pairs in this cycle so we
        # only hit the news providers once per name even when a name appears
        # in several candidate pairs.
        news_cache: dict[str, list[str]] = {}

        def _headlines_for(ticker: str) -> list[str]:
            if ticker in news_cache:
                return news_cache[ticker]
            try:
                items = news_service.fetch_and_persist(
                    ticker, as_of=as_of, lookback_days=30
                ) or []
            except Exception as exc:
                log.warning("news fetch failed for %s: %s", ticker, exc)
                items = []
            news_cache[ticker] = [it.headline for it in items[:6]]
            return news_cache[ticker]

        for c in top_pool:
            if len(accepted) >= available_slots:
                break
            news_for_pair = {
                c.leg_a: _headlines_for(c.leg_a),
                c.leg_b: _headlines_for(c.leg_b),
            }

            # P2l: wrap each debate_pair call in a Run audit row so the
            # transcript is reachable from the Runs UI. Idempotent on
            # (target, candidate_key, side='pair').
            pair_key = f"{c.leg_a}/{c.leg_b}"
            existing_link = (
                _PortfolioTargetRun.objects.select_related("run")
                .filter(target=audit_target, candidate_key=pair_key, side="pair")
                .first()
            )
            if existing_link is not None:
                audit_run = existing_link.run
            else:
                audit_run = _Run.objects.create(
                    user_id=strategy.user_id,
                    tickers=[c.leg_a, c.leg_b],
                    status=_Run.RUNNING,
                    model_overrides=overrides,
                    as_of_date=as_of,
                    personas=personas,
                    source=_Run.STRATEGY,
                    portfolio_target=audit_target,
                )
                _PortfolioTargetRun.objects.create(
                    target=audit_target,
                    run=audit_run,
                    candidate_key=pair_key,
                    primary_ticker=c.leg_a,
                    side="pair",
                    borrow_veto=False,
                    screener_rank=len(council_log) + 1,
                    screener_score=float(c.z_current),
                    sector=c.sector,
                    candidate_payload={
                        "leg_a": c.leg_a, "leg_b": c.leg_b,
                        "sector": c.sector,
                        "z_current": float(c.z_current),
                        "correlation": float(c.correlation),
                        "p_value": float(c.p_value),
                    },
                )

            try:
                decision = debate_pair(
                    leg_a=c.leg_a, leg_b=c.leg_b, sector=c.sector, as_of=as_of,
                    z_current=c.z_current, correlation=c.correlation, p_value=c.p_value,
                    personas=personas, model_overrides=overrides,
                    user_id=strategy.user_id,
                    portfolio_target_id=audit_target.id,
                    run_id=audit_run.id,
                    news_by_ticker=news_for_pair,
                )
            except Exception as exc:
                log.exception("pair council failed for %s/%s", c.leg_a, c.leg_b)
                audit_run.status = _Run.FAILED
                audit_run.error_message = f"pair council error: {exc}"[:2000]
                audit_run.finished_at = timezone.now()
                audit_run.save(update_fields=["status", "error_message", "finished_at"])
                continue

            # Persist the council output on the audit Run: one AgentMessage
            # carrying the votes + thesis, one Decision with action enter/skip.
            _AgentMessage.objects.update_or_create(
                run=audit_run, agent_name="pair_council",
                defaults={
                    "parsed_output": {
                        "votes": [v.model_dump() for v in decision.votes],
                        "thesis": decision.thesis,
                        "enter_count": decision.enter_count,
                        "skip_count": decision.skip_count,
                        "aggregate_confidence": decision.aggregate_confidence,
                        "news_counts": {
                            c.leg_a: len(news_for_pair[c.leg_a]),
                            c.leg_b: len(news_for_pair[c.leg_b]),
                        },
                    },
                    "status": "ok",
                },
            )
            _Decision.objects.update_or_create(
                run=audit_run, ticker=pair_key,
                defaults={
                    "action": decision.action,
                    "confidence": int(round(decision.aggregate_confidence * 100)),
                    "rationale": decision.thesis,
                    "side": "pair",
                    "target_weight_signed": _Dec("0"),
                },
            )
            from . import runs_bridge as _rb
            _rb.reconcile_run_cost(audit_run)
            audit_run.status = _Run.DONE
            audit_run.finished_at = timezone.now()
            audit_run.save(update_fields=["status", "finished_at"])

            council_log.append({
                "leg_a": c.leg_a, "leg_b": c.leg_b,
                "action": decision.action,
                "confidence": decision.aggregate_confidence,
                "enter_count": decision.enter_count,
                "skip_count": decision.skip_count,
                "thesis_excerpt": decision.thesis[:300],
                "news_counts": {
                    c.leg_a: len(news_for_pair[c.leg_a]),
                    c.leg_b: len(news_for_pair[c.leg_b]),
                },
                "run_id": audit_run.id,
            })
            if decision.action != "enter":
                continue
            if decision.aggregate_confidence < min_conf:
                continue
            accepted.append((c, decision))
    else:
        accepted = [(c, None) for c in top_pool[:available_slots]]

    # 4) Borrow-locate veto on the short leg. If the short leg can't be
    #    located, the whole pair is rejected — pairs trading needs both legs.
    borrow = StubBorrowProvider()
    borrow_log: list[dict] = []
    locatable_accepted: list = []
    for c, decision in accepted:
        quote = borrow.quote(c.leg_b, as_of)
        borrow.persist(quote)
        if not quote.is_locatable:
            borrow_log.append({
                "leg_a": c.leg_a, "leg_b": c.leg_b,
                "reason": "borrow_not_locatable", "fee_pct_annual": float(quote.fee_pct_annual),
            })
            continue
        locatable_accepted.append((c, decision))
    accepted = locatable_accepted

    # 5) Promote candidate Pair rows to status="open". When a candidate
    # row was persisted in step 2 we update it in place (preserving the
    # screener history); otherwise we create a new row.
    new_pair_rows: list[Pair] = []
    for c, decision in accepted:
        z_entry = c.z_current
        existing = candidate_pair_rows.get((c.leg_a, c.leg_b))
        update_fields = {
            "sector": c.sector,
            "cointegration_p_value": c.p_value,
            "correlation": c.correlation,
            "hedge_ratio": c.hedge_ratio,
            "spread_mean": c.spread_mean,
            "spread_std": c.spread_std,
            "spread_window_days": lookback,
            "entry_date": as_of,
            "entry_z": z_entry,
            "status": "open",
            "council_action": (decision.action if decision else ""),
            "council_confidence": (
                decision.aggregate_confidence if decision else None
            ),
            "council_thesis": (decision.thesis if decision else ""),
            "council_votes": (
                [v.model_dump() for v in decision.votes] if decision else []
            ),
        }
        if existing is not None:
            for k, v in update_fields.items():
                setattr(existing, k, v)
            existing.save()
            row = existing
        else:
            row = Pair.objects.create(
                strategy=strategy,
                leg_a_ticker=c.leg_a, leg_b_ticker=c.leg_b,
                **update_fields,
            )
        new_pair_rows.append(row)

    # 4) Build target_weights per pair (long leg_a +X, short leg_b -X·β),
    #    sum across pairs (a name may appear in multiple).
    #
    # P02k review: ``pair_notional_pct`` is the **long-leg notional per pair**
    # as a fraction of NAV. The long leg gets ``notional_pct / 2``; the
    # short leg gets ``- (notional_pct / 2) * hedge_ratio``. When
    # hedge_ratio==1 this yields a gross of ``notional_pct`` per pair
    # (dollar-neutral). When hedge_ratio≠1 the gross is
    # ``notional_pct/2 * (1 + hedge_ratio)`` — the *long* notional is
    # what the user controls, the short leg scales with the ratio so the
    # pair stays cointegration-neutral.
    notional_pct = float(strategy.pair_notional_pct)
    target_weights: dict[str, float] = {}
    pair_legs: list[tuple[Pair, str, float]] = []  # (pair, ticker, signed_weight)

    def _add_leg(book: dict[str, float], t: str, w: float) -> None:
        book[t] = book.get(t, 0.0) + w

    all_active = held_pairs + new_pair_rows
    for p in all_active:
        leg_a_w = notional_pct / 2.0
        leg_b_w = -leg_a_w * max(0.05, min(20.0, float(p.hedge_ratio)))
        _add_leg(target_weights, p.leg_a_ticker, leg_a_w)
        _add_leg(target_weights, p.leg_b_ticker, leg_b_w)
        pair_legs.append((p, p.leg_a_ticker, leg_a_w))
        pair_legs.append((p, p.leg_b_ticker, leg_b_w))

    # Snapshot today's z + spread per active pair (UI sparkline / post-mortem).
    z_history_by_pair: dict[int, list[float]] = {}
    for p in all_active:
        la = bundle.log_prices.get(p.leg_a_ticker) or []
        lb = bundle.log_prices.get(p.leg_b_ticker) or []
        if not la or not lb or p.spread_std <= 0:
            continue
        spread_today = la[-1] - float(p.hedge_ratio) * lb[-1]
        z_today = (spread_today - float(p.spread_mean)) / float(p.spread_std)
        try:
            import math
            PairZHistory.objects.update_or_create(
                pair=p, as_of_date=as_of,
                defaults={
                    "z": z_today,
                    "spread": spread_today,
                    "leg_a_close": Decimal(str(round(math.exp(la[-1]), 4))),
                    "leg_b_close": Decimal(str(round(math.exp(lb[-1]), 4))),
                },
            )
        except Exception as exc:
            log.warning("z-history write failed for pair %s: %s", p.pk, exc)
        # 30-day rolling slice for the UI sparkline.
        recent = list(
            PairZHistory.objects.filter(pair=p)
            .order_by("-as_of_date")[:30]
            .values_list("z", flat=True)
        )
        z_history_by_pair[p.pk] = list(reversed([float(v) for v in recent]))

    # 5) Persist PortfolioTarget + orders. On a rerun, the early audit_target
    # call already created the fresh superseding row; this reuses it.
    with transaction.atomic():
        target = _resolve_cycle_target(
            strategy, as_of,
            supersedes_target_id=supersedes_target_id,
            defaults={
                "status": "running",
                "target_weights": {},
                "rejected_candidates": [],
                "decisions": [],
                "error_message": "",
                "finished_at": None,
            },
        )

    last_close: dict[str, float] = {}
    existing_tickers = list(
        Position.objects.filter(portfolio=portfolio).values_list("ticker", flat=True)
    )
    for t in set(list(target_weights.keys()) + existing_tickers):
        try:
            bars = data_provider.get_daily_bars(
                t, start=as_of, end=as_of, as_of=as_of
            ) or data_provider.get_daily_bars(
                t, start=as_of.replace(day=1), end=as_of, as_of=as_of
            )
            if bars:
                last_close[t] = float(bars[-1].close)
        except Exception:
            last_close[t] = 0.0

    current = [
        CurrentPosition(
            ticker=p.ticker, quantity=float(p.quantity),
            avg_cost=float(p.avg_cost), sector=p.sector,
        )
        for p in Position.objects.filter(portfolio=portfolio)
    ]
    portfolio_value = float(portfolio.cash_balance) + sum(
        last_close.get(p.ticker, float(p.avg_cost)) * float(p.quantity)
        for p in Position.objects.filter(portfolio=portfolio)
    )

    orders = compute_orders(
        current,
        target_weights,
        RebalanceConfig(
            portfolio_value=max(1.0, portfolio_value),
            last_close=last_close,
            min_trade_notional_usd=float(strategy.min_trade_notional_usd),
            max_turnover_pct=float(strategy.max_turnover_pct),
        ),
    )

    # Attach the most relevant Pair to each order. For (open) pairs we link
    # by (ticker, long/short) inferred from their target weight; for pairs
    # closed this cycle we link by leg role so the two close orders both
    # carry the same pair_id (atomic-close contract).
    pair_by_leg: dict[tuple[str, str], Pair] = {}
    for p, t, w in pair_legs:
        pair_by_leg.setdefault((t, "long" if w > 0 else "short"), p)
    # Re-load the just-closed Pair rows to give close orders a back-link.
    closed_this_cycle = list(
        Pair.objects.filter(strategy=strategy, exit_date=as_of, status="closed")
    )
    closed_by_ticker: dict[str, Pair] = {}
    for cp in closed_this_cycle:
        closed_by_ticker.setdefault(cp.leg_a_ticker, cp)
        closed_by_ticker.setdefault(cp.leg_b_ticker, cp)

    # P02k review: pair-atomic order construction. After generic
    # compute_orders has run, scan back for pair links and enforce the
    # invariant that either BOTH legs of a pair generate orders or
    # NEITHER does. If only one leg survives (e.g. the other was below
    # min_trade_notional), drop the orphan and emit a diagnostic.
    pair_order_counts: dict[int, int] = {}
    pair_diagnostic_rows: list[dict] = []
    annotated_orders: list[tuple] = []  # (order, pair)
    for o in orders:
        sign = "long" if o.side in ("buy", "sell") else "short"
        pair = pair_by_leg.get((o.ticker, sign))
        if pair is None and o.reason == "close":
            pair = closed_by_ticker.get(o.ticker)
        if pair is not None:
            pair_order_counts[pair.id] = pair_order_counts.get(pair.id, 0) + 1
        annotated_orders.append((o, pair))

    final_orders: list[tuple] = []
    for o, pair in annotated_orders:
        if pair is not None and pair_order_counts.get(pair.id, 0) < 2:
            # Pair has only one surviving leg — atomic invariant broken.
            # Skip this order and record the diagnostic.
            pair_diagnostic_rows.append({
                "pair_id": pair.id,
                "pair_label": f"{pair.leg_a_ticker}/{pair.leg_b_ticker}",
                "orphan_ticker": o.ticker,
                "orphan_side": o.side,
                "reason": "single_leg_dropped_for_pair_atomicity",
            })
            continue
        final_orders.append((o, pair))

    RebalanceOrder.objects.filter(target=target).delete()
    rows: list[RebalanceOrder] = []
    for o, pair in final_orders:
        rows.append(RebalanceOrder(
            target=target, ticker=o.ticker, side=o.side,
            quantity=Decimal(str(round(o.quantity, 6))),
            limit_price=Decimal(str(round(o.limit_price, 4))) if o.limit_price else None,
            reason=o.reason,
            estimated_notional_usd=Decimal(str(round(o.estimated_notional_usd, 2))),
            sequence=o.sequence,
            pair=pair,
        ))
    RebalanceOrder.objects.bulk_create(rows)

    cycle_outcome = "target_created" if target_weights else "held_existing_book"
    target.target_weights = {t: round(w, 6) for t, w in target_weights.items()}
    target.gross_pct = Decimal(str(round(sum(abs(w) for w in target_weights.values()), 4)))
    target.net_pct = Decimal(str(round(sum(target_weights.values()), 4)))
    target.realised_net_pct = target.net_pct
    target.sector_exposure = {}
    target.rejected_candidates = []
    target.decisions = []
    # P02k review: open-pair diagnostics include days_held, latest z, P&L
    # estimate, and stop / regime status so the UI can render the open
    # pairs table without extra round-trips.
    def _pair_pnl(p: Pair) -> float:
        """Cheap mark-to-market P&L using last_close for each leg.

        Returns the % move in the spread from entry to today, signed so
        that "spread compressed = positive P&L" for the standard long-leg-A
        / short-leg-B sign convention.
        """
        if not p.entry_date or not p.spread_std or p.spread_std == 0:
            return 0.0
        leg_a_close = last_close.get(p.leg_a_ticker, 0.0)
        leg_b_close = last_close.get(p.leg_b_ticker, 0.0)
        if leg_a_close <= 0 or leg_b_close <= 0:
            return 0.0
        spread_now = leg_a_close - float(p.hedge_ratio) * leg_b_close
        # Entry spread is approximated as the historical mean (we don't store it).
        spread_entry = float(p.spread_mean)
        return (spread_entry - spread_now) / max(abs(spread_entry), 1e-6)

    target.beta_diagnostics = {
        **screener_diag,
        "n_open_before": len(open_pairs),
        "n_closed_this_cycle": len(closes_log),
        "n_new_pairs": len(new_pair_rows),
        "n_open_after": len(all_active),
        "n_candidates_persisted": len(candidate_pair_rows),
        "closes": closes_log,
        "council_enabled": council_on,
        "council_log": council_log,
        "borrow_vetoed_pairs": borrow_log,
        "atomic_pair_dropouts": pair_diagnostic_rows,
        "open_pairs": [
            {
                "id": p.pk,
                "leg_a": p.leg_a_ticker, "leg_b": p.leg_b_ticker,
                "sector": p.sector,
                "hedge_ratio": round(float(p.hedge_ratio), 4),
                "entry_z": round(float(p.entry_z or 0.0), 3),
                "p_value": round(float(p.cointegration_p_value), 4),
                "correlation": round(float(p.correlation), 3),
                "council_action": p.council_action,
                "council_confidence": p.council_confidence,
                "council_thesis": p.council_thesis,
                "z_history": z_history_by_pair.get(p.pk, []),
                # P02k review additions:
                "days_held": (
                    (as_of - p.entry_date).days if p.entry_date else None
                ),
                "pnl_pct": round(_pair_pnl(p) * 100.0, 2),
                "stop_z": float(strategy.pair_stop_z),
                "exit_z": float(strategy.pair_exit_z),
                "consecutive_coint_failures": int(p.consecutive_coint_failures or 0),
            }
            for p in all_active
        ],
    }
    # P3b council-alpha: pairs runs deterministic cointegration (no council by
    # default), so the baseline IS the realised book ⇒ council-alpha is 0.
    from apps.leaderboard.council_alpha import BASELINE_VERSION
    target.baseline_weights = target.target_weights
    target.baseline_version = BASELINE_VERSION
    target.cycle_outcome = cycle_outcome
    target.status = "done"
    target.finished_at = timezone.now()
    target.save()
    _maybe_auto_enroll(target)
    PortfolioStrategy.objects.filter(pk=strategy.pk).update(last_run_at=timezone.now())
    return {"target_id": target.pk, "orders": len(orders), "status": "done"}


@shared_task
def daily_long_short_cycle(
    strategy_id: int,
    as_of_date: str | None = None,
    *,
    force: bool = False,
    supersedes_target_id: int | None = None,
    override_preset: str | None = None,
    override_models: dict[str, str] | None = None,
) -> dict:
    strategy = PortfolioStrategy.objects.select_related("universe", "portfolio").get(
        pk=strategy_id
    )
    as_of = _resolve_as_of(as_of_date)

    if not force:
        # P2l: an awaiting_review target is also "in flight" — don't start
        # a fresh cycle while the user has one open for the same day.
        existing = PortfolioTarget.objects.filter(
            strategy=strategy, as_of_date=as_of,
            status__in=(
                PortfolioTarget.DONE,
                PortfolioTarget.AWAITING_REVIEW,
                PortfolioTarget.RUNNING_COUNCIL,
                PortfolioTarget.CONSTRUCTING,
                PortfolioTarget.RUNNING,
            ),
        ).first()
        if existing:
            return {"target_id": existing.pk, "status": "reused"}
    else:
        # force=True: cancel any non-terminal existing target so the partial
        # unique constraint allows a fresh row.
        PortfolioTarget.objects.filter(
            strategy=strategy, as_of_date=as_of,
            status__in=tuple(PortfolioTarget.ACTIVE_STATUSES),
        ).update(status=PortfolioTarget.CANCELLED, finished_at=timezone.now())

    members = _active_members(strategy, as_of)
    if not members:
        raise RuntimeError("Universe has no active members on as_of date.")

    # P10 §A1: the deterministic pods size off DailyBar momentum/vol, so a stale
    # or dividend-corrupt tail bar silently distorts the book. Refresh the recent
    # tail (the on-demand cache heuristic skips the newest sessions) and hard-
    # assert freshness + adjusted-close integrity before sizing; a failure raises
    # StaleMarketDataError so the cycle skips rather than trades on bad data.
    if strategy.kind in (
        PortfolioStrategy.KIND_RISK_PARITY,
        PortfolioStrategy.KIND_TREND,
        PortfolioStrategy.KIND_SECTOR_MOMENTUM,
    ):
        from apps.data.freshness import assert_universe_fresh, refresh_universe_bars

        _universe = [t for t, _ in members]
        refresh_universe_bars(_universe, as_of, get_fmp_provider(user=strategy.user))
        assert_universe_fresh(_universe, as_of)

    # Risk-parity fast path: pure deterministic inverse-vol — no screener, no
    # council. Drops the entire LLM cost (matches plan default
    # enable_council_veto=False). The veto-mode wiring is a follow-up.
    if strategy.kind == PortfolioStrategy.KIND_RISK_PARITY:
        # Pure deterministic inverse-vol — no council, no LLM calls — so the
        # dispatch-modal model/tier override has nothing to apply here and is
        # intentionally not threaded.
        return _run_risk_parity_cycle(
            strategy, as_of, members, supersedes_target_id=supersedes_target_id
        )

    if strategy.kind == PortfolioStrategy.KIND_PAIRS:
        return _run_pairs_cycle(
            strategy, as_of, members, supersedes_target_id=supersedes_target_id,
            override_preset=override_preset, override_models=override_models,
        )

    # Deterministic momentum fast path: trend (TSMOM) / sector momentum — no
    # council, no LLM. Like risk-parity, the model/tier override has nothing to
    # apply and is intentionally not threaded.
    if strategy.kind in (PortfolioStrategy.KIND_TREND, PortfolioStrategy.KIND_SECTOR_MOMENTUM):
        sizing = "tsmom" if strategy.kind == PortfolioStrategy.KIND_TREND else "xsec_momentum"
        return _run_momentum_cycle(
            strategy, as_of, members, sizing=sizing,
            supersedes_target_id=supersedes_target_id,
        )

    # P7c E4: news-sentiment single-name book + bounded persona conviction overlay.
    if strategy.kind == PortfolioStrategy.KIND_NEWS_SENTIMENT:
        return _run_news_sentiment_cycle(
            strategy, as_of, members, supersedes_target_id=supersedes_target_id,
        )

    try:
        # P2n: every screener call routes through a user-keyed FMP provider.
        screener_provider = get_fmp_provider(user=strategy.user)
        is_long_only_flavor = strategy.kind in (
            PortfolioStrategy.KIND_LONG_ONLY,
            PortfolioStrategy.KIND_CONCENTRATED_LONG,
            PortfolioStrategy.KIND_SECTOR_ROTATION,
            PortfolioStrategy.KIND_GLOBAL_MACRO,
        )
        if strategy.kind == PortfolioStrategy.KIND_GLOBAL_MACRO:
            from apps.data.models import MacroSnapshot

            from .models import MacroETF
            etf_rows = {e.ticker: e for e in MacroETF.objects.filter(is_active=True)}
            etfs_payload = []
            for ticker, sector in members:
                row = etf_rows.get(ticker)
                etfs_payload.append({
                    "ticker": ticker,
                    "sector": (row.asset_class if row else sector),
                    "theme": (row.direction if row else ""),
                    "affinities": row.regime_affinities if row else {},
                })
            snapshot = (
                MacroSnapshot.objects.filter(as_of_date__lte=as_of)
                .order_by("-as_of_date").first()
            )
            regime_vec = macro_regime_vector(snapshot)
            screener_out = run_sector_screener(
                etfs=etfs_payload,
                as_of_date=as_of,
                top_k=int(strategy.max_etfs_held) + 4,
                provider=screener_provider,
                benchmark="SPY",
                regime_vector=regime_vec,
                weights=strategy.screener_weights or None,
            )
        elif strategy.kind == PortfolioStrategy.KIND_SECTOR_ROTATION:
            from apps.data.models import MacroSnapshot

            from .models import SectorETF
            etf_rows = {e.ticker: e for e in SectorETF.objects.filter(is_active=True)}
            etfs_payload = []
            for ticker, sector in members:
                row = etf_rows.get(ticker)
                etfs_payload.append({
                    "ticker": ticker,
                    "sector": sector or (row.sector if row else ""),
                    "theme": row.theme if row else "",
                    "affinities": row.regime_affinities if row else {},
                })
            snapshot = (
                MacroSnapshot.objects.filter(as_of_date__lte=as_of)
                .order_by("-as_of_date").first()
            )
            regime_vec = macro_regime_vector(snapshot)
            screener_out = run_sector_screener(
                etfs=etfs_payload,
                as_of_date=as_of,
                top_k=int(strategy.max_etfs_held) + 4,
                provider=screener_provider,
                benchmark="SPY",
                regime_vector=regime_vec,
                weights=strategy.screener_weights or None,
            )
        else:
            screener_out = run_screener(
                members=members,
                as_of_date=as_of,
                top_k_longs=(
                    int(strategy.max_positions)
                    if strategy.kind == PortfolioStrategy.KIND_CONCENTRATED_LONG
                    else strategy.top_k_longs
                ),
                top_k_shorts=0 if is_long_only_flavor else strategy.top_k_shorts,
                provider=screener_provider,
                weights=strategy.screener_weights or None,
                long_only=is_long_only_flavor,
            )
    except ScreenerAbort as exc:
        raise RuntimeError(str(exc)) from exc

    # One transaction so the ranking row + target row appear atomically.
    # The partial unique constraint on (strategy, as_of_date) means a
    # concurrent dispatch lands in update_or_create's UPDATE branch instead
    # of creating a duplicate row.
    with transaction.atomic():
        ranking = ScreenerRanking.objects.create(
            strategy=strategy,
            as_of_date=as_of,
            long_candidates=screener_out["long_candidates"],
            short_candidates=screener_out["short_candidates"],
            universe_size_evaluated=screener_out["universe_size_evaluated"],
        )
        target = _resolve_cycle_target(
            strategy, as_of,
            supersedes_target_id=supersedes_target_id,
            defaults={
                "status": PortfolioTarget.SCREENING,
                "screener_ranking": ranking,
                "target_weights": {},
                "rejected_candidates": [],
                "decisions": [],
                "error_message": "",
                "finished_at": None,
                "total_cost_usd": Decimal("0"),
            },
        )

    # P2l: stop here if the user wants a review gate. The cycle persists
    # the ScreenerRanking + target in `awaiting_review` and waits for the
    # approval endpoint to dispatch council.
    if not bool(getattr(strategy, "auto_run_council", True)):
        PortfolioTarget.objects.filter(pk=target.pk).update(
            status=PortfolioTarget.AWAITING_REVIEW
        )
        return {
            "target_id": target.pk,
            "status": "awaiting_review",
            "n_long_candidates": len(screener_out["long_candidates"]),
            "n_short_candidates": len(screener_out["short_candidates"]),
        }

    # Auto-run path: cost-trim, build candidate Runs + payloads, dispatch.
    return _dispatch_council_chord(
        strategy=strategy,
        target=target,
        as_of=as_of,
        ranking=ranking,
        members=members,
        approved_longs=None,
        approved_shorts=None,
        override_preset=override_preset,
        override_models=override_models,
    )


def _resolve_flavor_and_personas(
    strategy: PortfolioStrategy, members: list[tuple[str, str]]
) -> tuple[bool, str, list[str] | None, float]:
    """Centralised ETF-graph + persona selection used by the cycle dispatcher.

    Returns (use_sector_graph, flavor_string, personas_for_run, bearish_veto_threshold).
    """
    from .models import MacroETF, SectorETF

    universe_tickers = [t for t, _ in members]
    etf_tickers = set(
        SectorETF.objects.filter(ticker__in=universe_tickers, is_active=True)
        .values_list("ticker", flat=True)
    ) | set(
        MacroETF.objects.filter(ticker__in=universe_tickers, is_active=True)
        .values_list("ticker", flat=True)
    )
    is_etf_universe = (
        len(universe_tickers) > 0
        and len(etf_tickers) >= len(universe_tickers) * 0.8
    )
    use_sector_graph = is_etf_universe or (
        strategy.kind == PortfolioStrategy.KIND_SECTOR_ROTATION
        and bool(getattr(strategy, "use_sector_council_v2", False))
    ) or strategy.kind == PortfolioStrategy.KIND_GLOBAL_MACRO

    if use_sector_graph:
        personas_for_run = strategy.personas or ["druckenmiller", "damodaran", "burry"]
    else:
        personas_for_run = strategy.personas or None
    bearish_veto_threshold = float(getattr(strategy, "bearish_veto_threshold", 0.70))
    flavor = "sector_rotation" if use_sector_graph else ""
    return use_sector_graph, flavor, personas_for_run, bearish_veto_threshold


def _dispatch_council_chord(
    *,
    strategy: PortfolioStrategy,
    target: PortfolioTarget,
    as_of: date_cls,
    ranking: ScreenerRanking,
    members: list[tuple[str, str]],
    approved_longs: list[str] | None,
    approved_shorts: list[str] | None,
    override_preset: str | None = None,
    override_models: dict[str, str] | None = None,
) -> dict:
    """P2l: create per-candidate Run rows + dispatch the council chord.

    Idempotent: if PortfolioTargetRun rows already exist for this target,
    reuse them rather than creating duplicates.

    `approved_longs` / `approved_shorts`:
      - None on the auto-run path → use the full trimmed candidate set.
      - lists on the manual-gate path → restrict to the approved subset.
    """
    from . import runs_bridge

    use_sector_graph, flavor, personas_for_run, bearish_veto_threshold = (
        _resolve_flavor_and_personas(strategy, members)
    )

    # Cost-ceiling trim only applies to auto-run; manual approval already
    # passed its own budget check at the endpoint.
    long_entries = list(ranking.long_candidates or [])
    short_entries = list(ranking.short_candidates or [])
    if approved_longs is None and approved_shorts is None:
        n_l = len(long_entries)
        n_s = len(short_entries)
        new_l, new_s = _trim_k_for_budget(
            strategy, n_l, n_s,
            preset=override_preset, overrides=override_models,
        )
        long_entries = long_entries[:new_l]
        short_entries = short_entries[:new_s]
        long_subset = None
        short_subset = None
    else:
        long_subset = approved_longs
        short_subset = approved_shorts

    # Override the side string for pure sector / global-macro flavors.
    is_sector_flavor = (
        use_sector_graph
        and strategy.kind in (
            PortfolioStrategy.KIND_SECTOR_ROTATION,
            PortfolioStrategy.KIND_GLOBAL_MACRO,
        )
    )

    # Re-package long/short into a CandidateSpec list via the bridge.
    # We build a synthetic ranking snapshot that respects any trimming.
    long_subset_keys = (
        [str(e.get("ticker", "")).upper() for e in long_entries]
        if long_subset is None
        else list(long_subset)
    )
    short_subset_keys = (
        [str(e.get("ticker", "")).upper() for e in short_entries]
        if short_subset is None
        else list(short_subset)
    )
    candidates, unknown = runs_bridge.candidates_from_ranking(
        ranking,
        long_subset=long_subset_keys,
        short_subset=short_subset_keys,
        is_sector_flavor=is_sector_flavor,
    )
    if unknown:
        # Should never happen on the auto-run path — defensive.
        log.warning("unknown candidates skipped on dispatch: %s", unknown)

    if not candidates:
        # Nothing actionable. Finalize with empty decisions so the UI still
        # shows a row.
        return finalize_cycle.run([], target.pk)

    overrides = _resolve_model_overrides(
        strategy, preset=override_preset, overrides=override_models
    )

    payloads, _runs = runs_bridge.create_candidate_runs(
        strategy=strategy,
        target=target,
        candidates=candidates,
        as_of=as_of,
        overrides=overrides,
        personas_for_run=personas_for_run,
        flavor=flavor,
        bearish_veto_threshold=bearish_veto_threshold,
    )

    PortfolioTarget.objects.filter(pk=target.pk).update(
        status=PortfolioTarget.RUNNING_COUNCIL
    )
    cb = finalize_cycle.s(target_id=target.pk)
    header = [run_candidate_council.s(p) for p in payloads]
    async_result = chord(header)(cb)
    PortfolioTarget.objects.filter(pk=target.pk).update(
        celery_task_id=str(async_result.id or "")
    )
    return {
        "target_id": target.pk,
        "status": "dispatched",
        "n_candidates": len(payloads),
        "run_ids": [p["run_id"] for p in payloads],
    }


def dispatch_approved_cycle(
    *,
    strategy: PortfolioStrategy,
    target: PortfolioTarget,
    approved_longs: list[str],
    approved_shorts: list[str],
) -> dict:
    """Public entry point used by the approve-council API.

    Pre-conditions (the view enforces these and returns 4xx if they fail):
      - target.status == awaiting_review
      - target.screener_ranking is not None
      - approval budget check has already passed
    """
    if target.screener_ranking_id is None:
        raise RuntimeError("target has no persisted ScreenerRanking to approve from")
    ranking = target.screener_ranking
    members = _active_members(strategy, target.as_of_date)
    return _dispatch_council_chord(
        strategy=strategy,
        target=target,
        as_of=target.as_of_date,
        ranking=ranking,
        members=members,
        approved_longs=approved_longs,
        approved_shorts=approved_shorts,
    )


# Register the P7 autopilot tasks + the P10 §E3 news-lab tasks. They live in
# sibling modules (`tasks_autopilot.py` / `tasks_lab.py`), which Celery's
# autodiscover_tasks() — it only imports each app's `tasks` module — would
# otherwise never load, leaving their beat entries (dispatch_due_autopilots /
# guardrail_sweep / fetch_lab_news / run_news_lab_cycles) rejected as
# "unregistered task" every cycle.
from . import tasks_autopilot, tasks_lab  # noqa: E402,F401
