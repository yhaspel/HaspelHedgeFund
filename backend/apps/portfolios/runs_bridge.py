"""P2l: bridge between strategy cycles and the ad-hoc Run lifecycle.

Responsibilities (per phase plan):

- Build candidate payloads for council fan-out (from ScreenerRanking + the
  approved subset, with optional borrow lookup for shorts).
- Re-run budget trimming/estimation before dispatch.
- Create queued Run rows and PortfolioTargetRun links idempotently.
- Populate graph payloads with both run_id and portfolio_target_id, so each
  LLMCall flowing through `record_llm_call` lights up both cost rollups.
- Persist strategy-council AgentMessage and Decision rows (the same shape
  ad-hoc Runs produce; consumed by the existing Runs UI).

The actual chord dispatch lives in `apps/portfolios/tasks.py` so this module
has no Celery dependency and remains test-friendly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as date_cls
from decimal import Decimal
from typing import Iterable

from django.db import transaction
from django.utils import timezone

from apps.runs.models import AgentMessage, Decision, Run

from .borrow import StubBorrowProvider
from .models import PortfolioStrategy, PortfolioTarget, PortfolioTargetRun, ScreenerRanking

log = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Candidate building from ScreenerRanking (used by both auto-run and approval).
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateSpec:
    """One screened candidate the council should debate."""
    ticker: str
    sector: str
    side: str            # long | short | sector
    theme: str
    rank: int
    score: float | None
    payload: dict        # the original screener entry, opaque to the bridge


def _coerce_score(entry: dict) -> float | None:
    raw = entry.get("score")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def candidates_from_ranking(
    ranking: ScreenerRanking,
    *,
    long_subset: Iterable[str] | None = None,
    short_subset: Iterable[str] | None = None,
    sector_subset: Iterable[str] | None = None,
    is_sector_flavor: bool = False,
) -> tuple[list[CandidateSpec], list[str]]:
    """Materialise CandidateSpec objects from a persisted ScreenerRanking.

    Subsets are *whitelists* applied to the ranking's already-ordered lists.
    If a subset is None, all entries on that side are accepted.
    If a subset is empty (e.g. [] for shorts), that side is suppressed.

    Returns (candidates, unknown_tickers). `unknown_tickers` flags entries
    the caller asked for that the ranking does not contain — surface this as
    a 400-style error in API code.
    """
    long_entries = list(ranking.long_candidates or [])
    short_entries = list(ranking.short_candidates or [])

    def _accept(entries: list[dict], subset: Iterable[str] | None) -> tuple[list[dict], list[str]]:
        if subset is None:
            return entries, []
        wanted = {str(t).strip().upper() for t in subset if t}
        if not wanted:
            return [], []
        known = {str(e.get("ticker", "")).strip().upper(): e for e in entries}
        unknown = sorted(wanted - set(known.keys()))
        accepted = [known[t] for t in wanted if t in known]
        # Preserve the order from the ranking itself, not the user's request,
        # so screener_rank stays meaningful.
        ordered = [e for e in entries if str(e.get("ticker", "")).strip().upper() in wanted]
        return ordered if accepted else [], unknown

    accepted_longs, unknown_longs = _accept(long_entries, long_subset)
    accepted_shorts, unknown_shorts = _accept(short_entries, short_subset)

    long_side = "sector" if is_sector_flavor else "long"
    cands: list[CandidateSpec] = []
    for idx, entry in enumerate(accepted_longs, start=1):
        cands.append(CandidateSpec(
            ticker=str(entry.get("ticker", "")).upper(),
            sector=str(entry.get("sector", "")),
            theme=str(entry.get("theme", "")),
            side=long_side,
            rank=idx,
            score=_coerce_score(entry),
            payload=dict(entry),
        ))
    # Sector subset overrides the long side when present.
    if sector_subset is not None and is_sector_flavor:
        # Already handled via long_subset.
        pass
    for idx, entry in enumerate(accepted_shorts, start=1):
        cands.append(CandidateSpec(
            ticker=str(entry.get("ticker", "")).upper(),
            sector=str(entry.get("sector", "")),
            theme=str(entry.get("theme", "")),
            side="short",
            rank=idx,
            score=_coerce_score(entry),
            payload=dict(entry),
        ))
    return cands, sorted(set(unknown_longs + unknown_shorts))


# -----------------------------------------------------------------------------
# Cost estimation for a specific candidate set (re-checked at approval time).
# -----------------------------------------------------------------------------


def estimate_candidates_cost(
    strategy: PortfolioStrategy, candidates: list[CandidateSpec]
) -> dict:
    """Estimate USD spend for `len(candidates)` council invocations.

    Uses the same per-agent token model as `tasks.estimate_cycle`, but pinned
    to the actual approved candidate count rather than top_k_*.
    """
    from apps.models_catalog.models import ModelEntry

    from .tasks import PER_AGENT_TOKEN_ESTIMATES, _resolve_model_overrides

    overrides = _resolve_model_overrides(strategy)
    prices = {m.id: m for m in ModelEntry.objects.all()}
    per_call_cost = 0.0
    per_agent = []
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
    n = len(candidates)
    est = per_call_cost * n
    cap = float(strategy.cost_ceiling_per_cycle_usd)
    return {
        "n_candidates": n,
        "per_call_usd": round(per_call_cost, 4),
        "est_total_usd": round(est, 4),
        "cost_ceiling_usd": cap,
        "exceeds_ceiling": (est > cap) if cap > 0 else False,
        "per_agent": per_agent,
        "overrides": overrides,
        "preset": strategy.model_preset,
    }


# -----------------------------------------------------------------------------
# Per-candidate Run + PortfolioTargetRun creation (idempotent).
# -----------------------------------------------------------------------------


def _candidate_key(spec: CandidateSpec) -> str:
    """For single-ticker candidates the key IS the ticker. Pair-council
    candidates use 'LEG_A/LEG_B'.
    """
    return spec.ticker


def _build_payload(
    *,
    strategy: PortfolioStrategy,
    target_id: int,
    run_id: int,
    spec: CandidateSpec,
    as_of: date_cls,
    overrides: dict,
    personas_for_run: list[str] | None,
    flavor: str,
    bearish_veto_threshold: float,
    borrow_veto: bool,
) -> dict:
    return {
        "run_id": run_id,
        "ticker": spec.ticker,
        "sector": spec.sector,
        "theme": spec.theme,
        "side": spec.side,
        "borrow_veto": borrow_veto,
        "as_of_date": as_of.isoformat(),
        "model_overrides": overrides,
        "personas": personas_for_run,
        "flavor": flavor,
        "bearish_veto_threshold": bearish_veto_threshold,
        "portfolio_target_id": target_id,
        "user_id": strategy.user_id,
    }


def create_candidate_runs(
    *,
    strategy: PortfolioStrategy,
    target: PortfolioTarget,
    candidates: list[CandidateSpec],
    as_of: date_cls,
    overrides: dict,
    personas_for_run: list[str] | None,
    flavor: str,
    bearish_veto_threshold: float,
) -> tuple[list[dict], list[Run]]:
    """Create one queued Run + PortfolioTargetRun per candidate (idempotent).

    Returns (payloads ready for the chord, ordered Run rows). If a
    (target, candidate_key, side) link already exists it is reused — the
    same payload is re-emitted with the existing run_id, so re-dispatch is
    safe after a partial failure.

    Short-side borrow lookup runs here so the borrow_veto flag is baked into
    the payload before the worker picks it up.
    """
    borrow = StubBorrowProvider()
    payloads: list[dict] = []
    runs: list[Run] = []

    with transaction.atomic():
        for spec in candidates:
            borrow_veto = False
            if spec.side == "short":
                quote = borrow.quote(spec.ticker, as_of)
                borrow.persist(quote)
                borrow_veto = not quote.is_locatable

            key = _candidate_key(spec)
            existing = (
                PortfolioTargetRun.objects.select_related("run")
                .filter(target=target, candidate_key=key, side=spec.side)
                .first()
            )
            if existing is not None:
                run = existing.run
                # Refresh borrow_veto if the link existed from a stale cycle.
                if existing.borrow_veto != borrow_veto:
                    existing.borrow_veto = borrow_veto
                    existing.save(update_fields=["borrow_veto"])
            else:
                run = Run.objects.create(
                    user_id=strategy.user_id,
                    tickers=[spec.ticker],
                    status=Run.QUEUED,
                    model_overrides=overrides,
                    as_of_date=as_of,
                    personas=list(personas_for_run or []),
                    source=Run.STRATEGY,
                    portfolio_target=target,
                )
                PortfolioTargetRun.objects.create(
                    target=target,
                    run=run,
                    candidate_key=key,
                    primary_ticker=spec.ticker,
                    side=spec.side,
                    borrow_veto=borrow_veto,
                    screener_rank=spec.rank,
                    screener_score=spec.score,
                    sector=spec.sector,
                    candidate_payload=spec.payload,
                )

            payloads.append(_build_payload(
                strategy=strategy,
                target_id=target.id,
                run_id=run.id,
                spec=spec,
                as_of=as_of,
                overrides=overrides,
                personas_for_run=personas_for_run,
                flavor=flavor,
                bearish_veto_threshold=bearish_veto_threshold,
                borrow_veto=borrow_veto,
            ))
            runs.append(run)
    return payloads, runs


# -----------------------------------------------------------------------------
# Strategy-council Run output persistence (mirrors apps/runs/tasks._persist_outputs
# but side-aware + idempotent).
# -----------------------------------------------------------------------------


# Same default agent set as the ad-hoc Run path.
def _agent_keys(personas: list[str]) -> list[str]:
    from hedgefund_agents.graphs.council import ANALYTICAL_NODES
    return list(ANALYTICAL_NODES.keys()) + list(personas) + ["risk", "pm_decision", "cio"]


def persist_council_outputs(
    *,
    run: Run,
    state: dict,
    selected_personas: list[str],
    side: str,
) -> None:
    """Mirror of `apps/runs/tasks._persist_outputs` but tagged with `side`
    for strategy-sourced runs. Idempotent: each agent's parsed_output is
    upserted by `(run, agent_name)`.
    """
    keys = _agent_keys(selected_personas)
    for agent_name in keys:
        payload = state.get(agent_name)
        if payload is None:
            continue
        AgentMessage.objects.update_or_create(
            run=run,
            agent_name=agent_name,
            defaults={"parsed_output": payload, "status": "ok"},
        )

    decision = state.get("decision")
    if decision:
        # signed weight: long positive, short negative (for short side runs).
        target_weight_pct = Decimal(str(decision.get("target_weight_pct", 0)))
        if side == "short":
            signed = -target_weight_pct
        else:
            signed = target_weight_pct
        Decision.objects.update_or_create(
            run=run,
            ticker=decision["ticker"],
            defaults={
                "action": decision["action"],
                "confidence": int(decision.get("aggregate_confidence", 0)),
                "rationale": decision.get("rationale", ""),
                "dissenting_views": decision.get("dissenting_personas", []),
                "target_quantity": Decimal(str(decision.get("target_quantity", 0))),
                "target_weight_pct": target_weight_pct,
                "risk_overrides": state.get("risk", {}),
                "side": side,
                "target_weight_signed": signed,
            },
        )


def reconcile_run_cost(run: Run) -> Decimal:
    """Aggregate Run.total_cost_usd from priced LLMCall rows.

    Defensive reconciliation: `record_llm_call` increments the row inline,
    but a worker crash mid-call could leave the inline counter stale.
    """
    from django.db.models import Sum
    from hedgefund_agents.models import LLMCall

    total = (
        LLMCall.objects.filter(run=run, cost_usd__gt=0)
        .aggregate(s=Sum("cost_usd"))["s"]
        or Decimal("0")
    )
    Run.objects.filter(pk=run.pk).update(total_cost_usd=total)
    return total


def mark_run(run: Run, status: str, *, error_message: str = "") -> None:
    """Terminal status helper; sets finished_at when leaving an active state."""
    fields: dict = {"status": status}
    if status in (Run.DONE, Run.FAILED, Run.CANCELLED):
        fields["finished_at"] = timezone.now()
    if error_message:
        fields["error_message"] = error_message[:2000]
    for k, v in fields.items():
        setattr(run, k, v)
    run.save(update_fields=list(fields.keys()))
