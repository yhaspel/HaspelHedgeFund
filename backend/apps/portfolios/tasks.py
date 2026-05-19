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
from datetime import date as date_cls, datetime
from decimal import Decimal

from celery import chord, shared_task
from django.db.models import Q
from django.utils import timezone

from hedgefund_agents.graphs.council import build_council_graph
from hedgefund_agents.registry import get_data_provider, get_filings_provider
from hedgefund_agents.screener.screener_agent import ScreenerAbort, run_screener

from .borrow import StubBorrowProvider
from .construction import Candidate, Constraints, construct
from .models import (
    PortfolioStrategy,
    PortfolioTarget,
    Position,
    RebalanceOrder,
    ScreenerRanking,
    UniverseMembership,
)
from .rebalance import CurrentPosition, RebalanceConfig, compute_orders

log = logging.getLogger(__name__)


def _resolve_as_of(s: str | None) -> date_cls:
    if not s:
        return timezone.now().date()
    if isinstance(s, date_cls):
        return s
    return datetime.fromisoformat(str(s)).date()


def _active_members(strategy: PortfolioStrategy, as_of: date_cls) -> list[tuple[str, str]]:
    qs = UniverseMembership.objects.filter(
        universe=strategy.universe,
        effective_from__lte=as_of,
    ).filter(Q(effective_to__isnull=True) | Q(effective_to__gt=as_of))
    return [(m.ticker, m.sector) for m in qs]


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


@shared_task
def run_candidate_council(payload: dict) -> dict:
    """Sub-task: run the full council on one candidate.

    Returns a dict suitable for the chord callback (the decision + side metadata)."""
    from hedgefund.celery import app  # avoid circular at import time
    ticker = payload["ticker"]
    sector = payload.get("sector", "")
    side = payload["side"]  # "long" | "short"
    borrow_veto = payload.get("borrow_veto", False)
    as_of = _resolve_as_of(payload["as_of_date"])
    personas = payload.get("personas") or None

    graph = build_council_graph(personas=personas)
    initial_state: dict = {
        "ticker": ticker,
        "as_of_date": as_of,
        "model_overrides": payload.get("model_overrides", {}),
        "data_provider": get_data_provider(),
        "filings_provider": get_filings_provider(),
        # P2e: tell PM/RM this is a short candidate so the action mapping
        # produces open_short instead of just sell, and so the borrow veto
        # propagates. Looser thresholds because the screener already
        # pre-filtered for attractiveness — we want the council to confirm,
        # not re-justify from zero.
        "pm_config": {
            "short_side": (side == "short"),
            "buy_threshold": 0.10,
            "sell_threshold": -0.10,
        },
        "borrow_veto": borrow_veto,
        "disable_cio": True,
    }
    try:
        final = graph.invoke(initial_state)
        decision = final.get("decision") or {}
        risk = final.get("risk") or {}
    except Exception as exc:  # pragma: no cover
        log.exception("council failed for %s", ticker)
        decision = {"ticker": ticker, "action": "hold", "rationale": f"council error: {exc}"}
        risk = {}
    return {
        "ticker": ticker,
        "sector": sector,
        "side": side,
        "borrow_veto": borrow_veto,
        "decision": decision,
        "risk": risk,
    }


@shared_task
def finalize_cycle(council_results: list[dict], target_id: int) -> dict:
    target = PortfolioTarget.objects.select_related("strategy", "strategy__portfolio").get(
        pk=target_id
    )
    strategy = target.strategy
    portfolio = strategy.portfolio
    as_of = target.as_of_date

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

    result = construct(cands, Constraints(
        target_gross_pct=float(strategy.target_gross_pct),
        target_net_pct=float(strategy.target_net_pct),
        max_position_pct=float(strategy.max_position_pct),
        max_sector_pct=float(strategy.max_sector_pct),
        min_position_pct=float(strategy.min_position_pct),
    ))

    # Pull last close per ticker for the rebalancer.
    data_provider = get_data_provider()
    last_close: dict[str, float] = {}
    for t in set(list(result.target_weights.keys())
                 + list(Position.objects.filter(portfolio=portfolio).values_list("ticker", flat=True))):
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
    target.sector_exposure = {s: round(w, 6) for s, w in result.sector_exposure.items()}
    target.rejected_candidates = result.rejected
    target.decisions = council_results
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
        existing = PortfolioTarget.objects.filter(
            strategy=strategy, as_of_date=as_of, status="done"
        ).first()
        if existing:
            return {"target_id": existing.pk, "status": "reused"}

    members = _active_members(strategy, as_of)
    if not members:
        raise RuntimeError("Universe has no active members on as_of date.")

    try:
        screener_out = run_screener(
            members=members,
            as_of_date=as_of,
            top_k_longs=strategy.top_k_longs,
            top_k_shorts=strategy.top_k_shorts,
            weights=strategy.screener_weights or None,
        )
    except ScreenerAbort as exc:
        raise RuntimeError(str(exc)) from exc

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
            "status": "running",
            "screener_ranking": ranking,
            "target_weights": {},
            "rejected_candidates": [],
            "decisions": [],
            "error_message": "",
            "finished_at": None,
        },
    )

    # Cost-ceiling trim.
    n_l = len(screener_out["long_candidates"])
    n_s = len(screener_out["short_candidates"])
    new_l, new_s = _trim_k_for_budget(strategy, n_l, n_s)
    long_cands = screener_out["long_candidates"][:new_l]
    short_cands = screener_out["short_candidates"][:new_s]

    # Borrow quotes for short side.
    borrow = StubBorrowProvider()
    short_payloads = []
    for c in short_cands:
        info = borrow.quote(c["ticker"], as_of)
        borrow.persist(info)
        short_payloads.append({
            "ticker": c["ticker"],
            "sector": c.get("sector", ""),
            "side": "short",
            "borrow_veto": (not info.is_locatable),
            "as_of_date": as_of.isoformat(),
            "model_overrides": {},
            "personas": strategy.personas or None,
        })
    long_payloads = [
        {
            "ticker": c["ticker"],
            "sector": c.get("sector", ""),
            "side": "long",
            "borrow_veto": False,
            "as_of_date": as_of.isoformat(),
            "model_overrides": {},
            "personas": strategy.personas or None,
        }
        for c in long_cands
    ]

    payloads = long_payloads + short_payloads
    if not payloads:
        # Nothing actionable. Finalize with empty decisions so the UI still
        # shows a row.
        return finalize_cycle.run([], target.pk)

    # Celery-chord parallel fan-out: each council call is a separate task;
    # `finalize_cycle` runs once all sub-tasks complete.
    cb = finalize_cycle.s(target_id=target.pk)
    header = [run_candidate_council.s(p) for p in payloads]
    async_result = chord(header)(cb)
    PortfolioTarget.objects.filter(pk=target.pk).update(
        celery_task_id=str(async_result.id or "")
    )
    return {"target_id": target.pk, "status": "dispatched", "n_candidates": len(payloads)}
