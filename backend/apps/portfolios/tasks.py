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
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.models_catalog.presets import expand_preset
from hedgefund_agents.graphs.council import build_council_graph, build_sector_council_graph
from hedgefund_agents.registry import get_data_provider, get_filings_provider
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


def _resolve_model_overrides(strategy: PortfolioStrategy) -> dict[str, str]:
    """Order of precedence:
      1. User's per-agent defaults from Settings → Models (the "Default model"
         selector populates this with the same model for every agent).
      2. The strategy's model_preset (e.g. 'frugal', 'hybrid') expanded into a
         per-agent map.
      3. Empty dict → registry DEFAULT_MODELS applies.
    """
    user_prefs = getattr(strategy.user, "model_prefs", None)
    if user_prefs and user_prefs.per_agent_defaults:
        return dict(user_prefs.per_agent_defaults)
    preset_map = expand_preset(strategy.model_preset or "hybrid")
    return preset_map or {}


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


def estimate_cycle(strategy: PortfolioStrategy) -> dict:
    """Pre-flight cost estimate for a manual `Run cycle now`.

    Returns the resolved per-agent model map plus the projected USD spend
    for `top_k_longs + top_k_shorts` council invocations. CIO is excluded
    because the cycle disables it (disable_cio=True)."""
    from apps.models_catalog.models import ModelEntry

    overrides = _resolve_model_overrides(strategy)
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
        "preset": strategy.model_preset,
    }


def _trim_k_for_budget(strategy: PortfolioStrategy, n_longs: int, n_shorts: int) -> tuple[int, int]:
    """Very rough cost estimate. Each council call ~ $0.05 on hybrid preset
    (3 frontier + 10 cheap LLM calls). Trim symmetrically until under cap.
    """
    per_call = 0.05
    cap = float(strategy.cost_ceiling_per_cycle_usd)
    if cap <= 0:
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


@shared_task(bind=True)
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
    initial_state: dict = {
        "ticker": ticker,
        "as_of_date": as_of,
        "model_overrides": payload.get("model_overrides", {}),
        "data_provider": get_data_provider(),
        "filings_provider": get_filings_provider(),
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

    portfolio_beta = 0.0
    beta_diagnostics: dict = {}
    cycle_outcome = "target_created"
    per_position_thesis: dict[str, dict] = {}
    if strategy.kind == PortfolioStrategy.KIND_GLOBAL_MACRO:
        from apps.data.models import MacroSnapshot

        from .models import MacroETF, MacroRegimeSnapshot
        etf_rows = {e.ticker: e for e in MacroETF.objects.filter(is_active=True)}
        asset_class_of = {t: e.asset_class for t, e in etf_rows.items()}
        inverse_of = {t: e.inverse_of for t, e in etf_rows.items() if e.inverse_of}
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
        beta_diagnostics = {
            "netted_pairs": result.netted_pairs,
            "asset_class_exposure": result.asset_class_exposure,
            "regime_vector": regime_vec,
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
        beta_diagnostics = {"overlap_dropped": result.overlap_dropped}
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
                }
    elif strategy.kind == PortfolioStrategy.KIND_MARKET_NEUTRAL:
        data_provider = get_data_provider()
        survivors = [c.ticker for c in cands if c.veto_reason is None]
        betas_full = compute_betas_for(
            survivors,
            benchmark=strategy.benchmark_ticker,
            as_of=as_of,
            window_days=int(strategy.beta_window_days),
            data_provider=data_provider,
        )
        beta_map: dict[str, float] = {}
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

        neutral = construct_market_neutral(
            cands, constraints, beta_map,
            tol_dollar=float(strategy.neutrality_tolerance_dollar_pct),
            tol_beta=float(strategy.neutrality_tolerance_beta),
        )
        beta_diagnostics = {
            **neutral.diagnostics,
            "unreliable": unreliable,
            "benchmark": strategy.benchmark_ticker,
            "window_days": int(strategy.beta_window_days),
            "n_betas": len(beta_map),
        }
        portfolio_beta = neutral.portfolio_beta
        result = neutral
    else:
        result = construct(cands, constraints)

    # Pull last close per ticker for the rebalancer.
    data_provider = get_data_provider()
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
    target.gross_pct = Decimal(str(round(result.gross_pct, 4)))
    target.net_pct = Decimal(str(round(result.net_pct, 4)))
    target.realised_net_pct = Decimal(str(round(result.net_pct, 4)))
    target.realised_portfolio_beta = Decimal(str(round(portfolio_beta, 3)))
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

    PortfolioStrategy.objects.filter(pk=strategy.pk).update(last_run_at=timezone.now())
    return {"target_id": target.pk, "orders": len(orders), "status": "done"}


def _run_risk_parity_cycle(
    strategy: PortfolioStrategy, as_of: date_cls, members: list[tuple[str, str]]
) -> dict:
    """Deterministic inverse-vol cycle. No LLM, no screener."""
    data_provider = get_data_provider()
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

    result = construct_risk_parity(
        members,
        vols,
        target_gross_pct=float(strategy.target_gross_pct or 1.0),
        per_sleeve_max_pct=float(strategy.per_etf_max_pct or 0.50),
        per_sleeve_min_pct=float(strategy.per_etf_min_pct or 0.02),
        current_weights=current_weights or None,
        rebalance_band_pct=float(strategy.rebalance_band_pct),
    )

    with transaction.atomic():
        target, _ = PortfolioTarget.objects.update_or_create(
            strategy=strategy, as_of_date=as_of,
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
    }
    target.cycle_outcome = cycle_outcome
    target.decisions = []
    target.status = "done"
    target.finished_at = timezone.now()
    target.save()
    PortfolioStrategy.objects.filter(pk=strategy.pk).update(last_run_at=timezone.now())
    return {"target_id": target.pk, "orders": len(orders), "status": "done"}


def _run_pairs_cycle(
    strategy: PortfolioStrategy, as_of: date_cls, members: list[tuple[str, str]]
) -> dict:
    """Deterministic pairs-trading cycle (no LLM, no council).

    Screen within-sector pairs by cointegration + correlation + |z| > entry.
    Close mean-reverted / stopped open pairs first; then fill remaining slots
    with top |z| new candidates. target_weights collapses paired legs into
    signed per-ticker weights (long_a positive, short_b = - hedge_ratio·notional).
    """
    data_provider = get_data_provider()
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

    # 3) Optional council: vet each candidate; drop skips + low-confidence enters.
    council_log: list[dict] = []
    accepted: list = []
    if council_on and top_pool:
        from decimal import Decimal as _Dec

        from apps.runs.models import AgentMessage as _AgentMessage
        from apps.runs.models import Decision as _Decision
        from apps.runs.models import Run as _Run
        from hedgefund_agents.pairs_council import debate_pair
        from hedgefund_agents.registry import get_news_service

        from .models import PortfolioTargetRun as _PortfolioTargetRun
        min_conf = float(strategy.pair_council_min_confidence)
        overrides = _resolve_model_overrides(strategy)
        personas = list(strategy.personas or [])
        news_service = get_news_service()
        # Ensure a target row exists so we can link Runs to it. The cycle's
        # target row is created later below via update_or_create — for pair
        # councils we need it earlier so audit Runs have a parent.
        with transaction.atomic():
            audit_target, _ = PortfolioTarget.objects.update_or_create(
                strategy=strategy, as_of_date=as_of,
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

    # 5) Persist new Pair rows.
    new_pair_rows: list[Pair] = []
    for c, decision in accepted:
        z_entry = c.z_current
        row = Pair.objects.create(
            strategy=strategy,
            leg_a_ticker=c.leg_a, leg_b_ticker=c.leg_b, sector=c.sector,
            cointegration_p_value=c.p_value, correlation=c.correlation,
            hedge_ratio=c.hedge_ratio, spread_mean=c.spread_mean, spread_std=c.spread_std,
            spread_window_days=lookback,
            entry_date=as_of, entry_z=z_entry,
            status="open",
            council_action=(decision.action if decision else ""),
            council_confidence=(decision.aggregate_confidence if decision else None),
            council_thesis=(decision.thesis if decision else ""),
            council_votes=([v.model_dump() for v in decision.votes] if decision else []),
        )
        new_pair_rows.append(row)

    # 4) Build target_weights per pair (long leg_a +X, short leg_b -X·β),
    #    sum across pairs (a name may appear in multiple).
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

    # 5) Persist PortfolioTarget + orders.
    with transaction.atomic():
        target, _ = PortfolioTarget.objects.update_or_create(
            strategy=strategy, as_of_date=as_of,
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

    RebalanceOrder.objects.filter(target=target).delete()
    rows: list[RebalanceOrder] = []
    for o in orders:
        sign = "long" if o.side in ("buy", "sell") else "short"
        pair = pair_by_leg.get((o.ticker, sign))
        if pair is None and o.reason == "close":
            pair = closed_by_ticker.get(o.ticker)
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
    target.beta_diagnostics = {
        **screener_diag,
        "n_open_before": len(open_pairs),
        "n_closed_this_cycle": len(closes_log),
        "n_new_pairs": len(new_pair_rows),
        "n_open_after": len(all_active),
        "closes": closes_log,
        "council_enabled": council_on,
        "council_log": council_log,
        "borrow_vetoed_pairs": borrow_log,
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
            }
            for p in all_active
        ],
    }
    target.cycle_outcome = cycle_outcome
    target.status = "done"
    target.finished_at = timezone.now()
    target.save()
    PortfolioStrategy.objects.filter(pk=strategy.pk).update(last_run_at=timezone.now())
    return {"target_id": target.pk, "orders": len(orders), "status": "done"}


@shared_task
def daily_long_short_cycle(
    strategy_id: int,
    as_of_date: str | None = None,
    *,
    force: bool = False,
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

    # Risk-parity fast path: pure deterministic inverse-vol — no screener, no
    # council. Drops the entire LLM cost (matches plan default
    # enable_council_veto=False). The veto-mode wiring is a follow-up.
    if strategy.kind == PortfolioStrategy.KIND_RISK_PARITY:
        return _run_risk_parity_cycle(strategy, as_of, members)

    if strategy.kind == PortfolioStrategy.KIND_PAIRS:
        return _run_pairs_cycle(strategy, as_of, members)

    try:
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
        target, _ = PortfolioTarget.objects.update_or_create(
            strategy=strategy, as_of_date=as_of,
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
        new_l, new_s = _trim_k_for_budget(strategy, n_l, n_s)
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

    overrides = _resolve_model_overrides(strategy)

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
