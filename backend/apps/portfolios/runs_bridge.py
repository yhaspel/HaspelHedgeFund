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
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date as date_cls
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from apps.runs.models import AgentMessage, Decision, Run

from .borrow import StubBorrowProvider
from .models import (
    LedgerEntry,
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    PortfolioTargetRun,
    Position,
    ScreenerRanking,
)
from .quantity_policy import QuantityPolicy, round_quantity_for_open

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


# -----------------------------------------------------------------------------
# P4 WS-E: Enter strategy — materialize a done cycle's target_weights into the
# strategy's portfolio book as real Position rows (delta-to-target), journaling
# one LedgerEntry per mutation. Mirrors the manual_book accounting but targets
# the *strategy* portfolio (manual_book is hardwired to the user's manual book).
# -----------------------------------------------------------------------------


class EnrollmentError(ValueError):
    """User-facing enrollment error carrying an HTTP status_code (default 409)."""

    def __init__(self, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class EnrollmentRow:
    ticker: str
    side: str                       # "long" | "short"
    target_weight_pct: float        # signed fraction from target_weights
    target_notional_usd: Decimal
    suggested_quantity: Decimal     # signed, post-rounding
    mark_price: Decimal | None
    mark_source: str                # "mark" | "limit_price" | "none"
    current_quantity: Decimal       # signed, existing book position (0 if none)
    current_avg_cost: Decimal | None
    action: str                     # open|increase|reduce|close|hold|skip
    quantity_delta: Decimal         # signed change applied on enroll
    rebalance_order_id: int | None
    source_run_id: int | None
    source_decision_id: int | None
    warnings: list[str]


@dataclass
class EnrollmentResult:
    target_id: int
    as_of_date: str
    portfolio: dict
    rows: list[EnrollmentRow]
    totals: dict
    enrolled: bool


def _q4(x: Decimal) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _money(x: Decimal) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _resolve_mark(ticker: str, *, user, limit_price: Decimal | None):
    """(price, source) for sizing + avg_cost. get_mark first, then the cycle's
    RebalanceOrder limit_price, else (None, 'none')."""
    from .valuation import get_mark

    try:
        mark = get_mark(ticker, user=user)
    except Exception:  # missing FMP key / provider error — fall back, never block
        mark = None
    if mark is not None and mark.price and mark.price > 0:
        return Decimal(str(mark.price)), ("mark_stale" if mark.stale else "mark")
    if limit_price and Decimal(str(limit_price)) > 0:
        return Decimal(str(limit_price)), "limit_price"
    return None, "none"


def _portfolio_dict(portfolio: Portfolio) -> dict:
    return {
        "id": portfolio.id,
        "name": portfolio.name,
        "kind": portfolio.kind,
        "cash": str(_money(portfolio.cash_balance)),
        "positions_count": portfolio.positions.count(),
    }


def _build_enrollment_rows(
    target: PortfolioTarget,
    portfolio: Portfolio,
    *,
    user,
    override_quantities: dict | None = None,
) -> list[EnrollmentRow]:
    """Pure computation of the enroll preview rows (no mutation).

    Sizes each target weight against the book NAV (cash + marked positions),
    rounds via quantity_policy (whole shares), flags rows below the strategy's
    min_trade_notional as skip, and diffs against existing positions to assign
    open / increase / reduce / hold / close actions. Positions held in the book
    but absent from target_weights become close rows.
    """
    strategy = target.strategy
    overrides = {k.upper(): v for k, v in (override_quantities or {}).items()}
    policy = QuantityPolicy.from_mode("whole")
    min_trade = Decimal(str(strategy.min_trade_notional_usd or 0))

    weights: dict[str, float] = dict(target.target_weights or {})
    order_by_ticker = {
        o.ticker.upper(): o for o in target.orders.all()
    }
    positions_by_ticker = {
        p.ticker.upper(): p for p in portfolio.positions.all()
    }

    # NAV = cash + Σ signed_qty × mark (mark falls back to avg_cost).
    nav = Decimal(str(portfolio.cash_balance))
    for pos in positions_by_ticker.values():
        price, _ = _resolve_mark(pos.ticker, user=user, limit_price=None)
        mark_px = price if price is not None else pos.avg_cost
        nav += pos.quantity * mark_px
    nav = _money(nav)

    rows: list[EnrollmentRow] = []

    for ticker in sorted(weights.keys()):
        ticker = ticker.upper()
        weight = float(weights[ticker])
        side = "long" if weight >= 0 else "short"
        order = order_by_ticker.get(ticker)
        limit_price = order.limit_price if order else None
        price, mark_source = _resolve_mark(ticker, user=user, limit_price=limit_price)
        warnings: list[str] = []

        ptr = (
            PortfolioTargetRun.objects.filter(target=target, primary_ticker=ticker)
            .select_related("run").first()
        )
        source_run_id = ptr.run_id if ptr else None
        source_decision = None
        if ptr is not None:
            source_decision = (
                Decision.objects.filter(run_id=ptr.run_id, ticker=ticker).first()
            )

        current = positions_by_ticker.get(ticker)
        current_qty = current.quantity if current else Decimal("0")
        current_avg = current.avg_cost if current else None

        target_notional = _money(abs(Decimal(str(weight))) * nav)

        if price is None:
            warnings.append("no_mark")
            rows.append(EnrollmentRow(
                ticker=ticker, side=side, target_weight_pct=weight,
                target_notional_usd=target_notional, suggested_quantity=Decimal("0"),
                mark_price=None, mark_source=mark_source,
                current_quantity=current_qty, current_avg_cost=current_avg,
                action="skip", quantity_delta=Decimal("0"),
                rebalance_order_id=order.id if order else None,
                source_run_id=source_run_id,
                source_decision_id=source_decision.id if source_decision else None,
                warnings=warnings,
            ))
            continue

        # Target magnitude: override (manual mode) wins over weight×NAV sizing.
        if ticker in overrides:
            try:
                target_abs = Decimal(str(overrides[ticker])).copy_abs()
                target_abs = round_quantity_for_open(target_abs, price, policy).quantity
            except Exception:
                target_abs = Decimal("0")
                warnings.append("bad_override")
        else:
            raw_qty = target_notional / price
            rq = round_quantity_for_open(raw_qty, price, policy)
            target_abs = rq.quantity
            if rq.warning:
                warnings.append("below_one_share")

        rounded_notional = _money(target_abs * price)
        if target_abs <= 0 or rounded_notional < min_trade:
            if "below_one_share" not in warnings:
                warnings.append("below_min_trade_notional")
            rows.append(EnrollmentRow(
                ticker=ticker, side=side, target_weight_pct=weight,
                target_notional_usd=target_notional, suggested_quantity=Decimal("0"),
                mark_price=_q4(price), mark_source=mark_source,
                current_quantity=current_qty, current_avg_cost=current_avg,
                action="skip", quantity_delta=Decimal("0"),
                rebalance_order_id=order.id if order else None,
                source_run_id=source_run_id,
                source_decision_id=source_decision.id if source_decision else None,
                warnings=warnings,
            ))
            continue

        target_signed = target_abs if side == "long" else -target_abs
        delta = target_signed - current_qty

        if current_qty == 0:
            action = "open"
        elif (current_qty > 0) != (target_signed > 0):
            action = "skip"  # side flip — defensive; close the existing first
            warnings.append("side_conflict")
            delta = Decimal("0")
        elif target_abs > current_qty.copy_abs():
            action = "increase"
        elif target_abs < current_qty.copy_abs():
            action = "reduce"
        else:
            action = "hold"
            delta = Decimal("0")

        rows.append(EnrollmentRow(
            ticker=ticker, side=side, target_weight_pct=weight,
            target_notional_usd=target_notional, suggested_quantity=target_signed,
            mark_price=_q4(price), mark_source=mark_source,
            current_quantity=current_qty, current_avg_cost=current_avg,
            action=action, quantity_delta=delta,
            rebalance_order_id=order.id if order else None,
            source_run_id=source_run_id,
            source_decision_id=source_decision.id if source_decision else None,
            warnings=warnings,
        ))

    # Book positions absent from target_weights → close to match the target.
    target_tickers = {t.upper() for t in weights}
    for ticker, pos in sorted(positions_by_ticker.items()):
        if ticker in target_tickers:
            continue
        order = order_by_ticker.get(ticker)
        limit_price = order.limit_price if order else None
        price, mark_source = _resolve_mark(ticker, user=user, limit_price=limit_price)
        warnings = []
        if price is None:
            # Last resort so a close can still settle: exit at avg_cost (0 pnl).
            price = pos.avg_cost
            warnings.append("no_mark")
        rows.append(EnrollmentRow(
            ticker=ticker,
            side="short" if pos.quantity < 0 else "long",
            target_weight_pct=0.0, target_notional_usd=Decimal("0"),
            suggested_quantity=Decimal("0"),
            mark_price=_q4(price),
            mark_source=mark_source,
            current_quantity=pos.quantity, current_avg_cost=pos.avg_cost,
            action="close", quantity_delta=-pos.quantity,
            rebalance_order_id=order.id if order else None,
            source_run_id=None, source_decision_id=None,
            warnings=warnings,
        ))

    return rows


def _totals(rows: list[EnrollmentRow]) -> dict:
    gross = sum(abs(r.target_weight_pct) for r in rows if r.action != "close")
    net = sum(r.target_weight_pct for r in rows if r.action != "close")
    return {
        "gross_pct": round(gross, 4),
        "net_pct": round(net, 4),
        "n_open": sum(1 for r in rows if r.action == "open"),
        "n_increase": sum(1 for r in rows if r.action == "increase"),
        "n_reduce": sum(1 for r in rows if r.action == "reduce"),
        "n_close": sum(1 for r in rows if r.action == "close"),
        "n_skip": sum(1 for r in rows if r.action in ("skip", "hold")),
    }


def preview_enrollment(target: PortfolioTarget, *, user) -> EnrollmentResult:
    """GET preview: rows + totals + portfolio snapshot. No mutation."""
    portfolio = target.strategy.portfolio
    if portfolio.kind == Portfolio.KIND_MANUAL:
        raise EnrollmentError("the Manual Book cannot receive strategy enrollment")
    rows = _build_enrollment_rows(target, portfolio, user=user)
    return EnrollmentResult(
        target_id=target.id,
        as_of_date=target.as_of_date.isoformat(),
        portfolio=_portfolio_dict(portfolio),
        rows=rows,
        totals=_totals(rows),
        enrolled=target.enrolled_at is not None,
    )


def enroll_target_into_portfolio(
    target: PortfolioTarget,
    *,
    mode: str,
    approved_tickers: list[str] | None = None,
    override_quantities: dict | None = None,
    note: str = "",
) -> EnrollmentResult:
    """POST apply: materialize the (approved) rows into the strategy portfolio.

    mode="auto"   → enroll every actionable row.
    mode="manual" → enroll only rows whose ticker is in approved_tickers.

    One LedgerEntry per mutation (strategy_enroll / _reduce / _close);
    opened_via="strategy_cycle"; source_run/source_decision stamped per ticker.
    Stamps target.enrolled_at + a frozen enrollment_diff snapshot.
    """
    if mode not in ("auto", "manual"):
        raise EnrollmentError("mode must be 'auto' or 'manual'", status_code=400)
    if target.status != PortfolioTarget.DONE:
        raise EnrollmentError(
            f"cycle is {target.status}, not done; only done cycles can be enrolled"
        )
    strategy = target.strategy
    user = strategy.user
    portfolio = strategy.portfolio
    if portfolio.kind == Portfolio.KIND_MANUAL:
        raise EnrollmentError("enrollment must never write into the Manual Book")

    approved = (
        None if mode == "auto"
        else {t.strip().upper() for t in (approved_tickers or [])}
    )

    with transaction.atomic():
        portfolio = (
            Portfolio.objects.select_for_update().get(pk=portfolio.pk)
        )
        rows = _build_enrollment_rows(
            target, portfolio, user=user, override_quantities=override_quantities,
        )
        diff: dict = {}
        applied_rows: list[EnrollmentRow] = []
        for row in rows:
            if row.action in ("skip", "hold"):
                continue
            if approved is not None and row.ticker not in approved:
                continue
            _apply_enrollment_row(portfolio, row, user=user, note=note)
            diff[row.ticker] = {
                "action": row.action,
                "quantity_delta": str(row.quantity_delta),
                "notional": str(row.target_notional_usd),
                "mark_price": str(row.mark_price) if row.mark_price is not None else None,
            }
            applied_rows.append(row)

        target.enrolled_at = timezone.now()
        target.enrollment_diff = diff
        target.save(update_fields=["enrolled_at", "enrollment_diff"])

    log.info(
        "strategy_enroll strategy_id=%s target_id=%s n_rows=%s mode=%s",
        strategy.id, target.id, len(applied_rows), mode,
    )
    portfolio.refresh_from_db()
    return EnrollmentResult(
        target_id=target.id,
        as_of_date=target.as_of_date.isoformat(),
        portfolio=_portfolio_dict(portfolio),
        rows=applied_rows,
        totals=_totals(applied_rows),
        enrolled=True,
    )


def _apply_enrollment_row(
    portfolio: Portfolio, row: EnrollmentRow, *, user, note: str,
) -> None:
    """Mutate one Position + write one LedgerEntry. Mirrors manual_book math."""
    price = row.mark_price
    ticker = row.ticker
    position = (
        Position.objects.select_for_update()
        .filter(portfolio=portfolio, ticker=ticker).first()
    )

    if row.action in ("open", "increase"):
        delta_abs = row.quantity_delta.copy_abs()
        long = row.side == "long"
        cash_delta = (-(delta_abs * price)) if long else (delta_abs * price)
        if position is None:
            position = Position.objects.create(
                portfolio=portfolio, ticker=ticker,
                quantity=row.quantity_delta, avg_cost=_q4(price), sector="",
                opened_via=Position.OPENED_VIA_STRATEGY_CYCLE,
                source_run_id=row.source_run_id,
                source_decision_id=row.source_decision_id,
                note=note,
            )
        else:
            old_abs = position.quantity.copy_abs()
            new_abs = old_abs + delta_abs
            position.avg_cost = _q4(
                (position.avg_cost * old_abs + price * delta_abs) / new_abs
            )
            position.quantity = position.quantity + row.quantity_delta
            position.save(update_fields=["quantity", "avg_cost"])
        kind = LedgerEntry.KIND_STRATEGY_ENROLL
        realized = Decimal("0")
    else:  # reduce | close
        reduce_abs = row.quantity_delta.copy_abs()
        is_short = (position.quantity < 0) if position else False
        if is_short:
            realized = (position.avg_cost - price) * reduce_abs
            cash_delta = -(reduce_abs * price)
        else:
            realized = (price - position.avg_cost) * reduce_abs
            cash_delta = reduce_abs * price
        realized = _money(realized)
        if row.action == "close":
            kind = LedgerEntry.KIND_STRATEGY_ENROLL_CLOSE
            position.realized_pnl = _money(position.realized_pnl + realized)
            position.save(update_fields=["realized_pnl"])
            position_for_source = position
            position.delete()
            position = None
        else:
            kind = LedgerEntry.KIND_STRATEGY_ENROLL_REDUCE
            position.quantity = position.quantity + row.quantity_delta
            position.realized_pnl = _money(position.realized_pnl + realized)
            position.save(update_fields=["quantity", "realized_pnl"])
            position_for_source = position

    cash_delta = _money(cash_delta)
    portfolio.cash_balance = _money(portfolio.cash_balance + cash_delta)
    portfolio.save(update_fields=["cash_balance"])

    src_run = row.source_run_id
    src_dec = row.source_decision_id
    if row.action in ("reduce", "close") and position_for_source is not None:
        src_run = src_run or position_for_source.source_run_id
        src_dec = src_dec or position_for_source.source_decision_id

    LedgerEntry.objects.create(
        portfolio=portfolio, kind=kind, ticker=ticker,
        quantity_delta=row.quantity_delta, price=price,
        cash_delta=cash_delta, realized_pnl=realized,
        quantity_after=(position.quantity if position is not None else Decimal("0")),
        cash_balance_after=portfolio.cash_balance,
        position=position,
        source_run_id=src_run, source_decision_id=src_dec,
        note=note, created_by=user,
    )
