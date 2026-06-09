"""Single-segment backtest executor.

Replays trading days in `[start, end]` against the agent graph (or against
cached agent outputs), applies PM aggregation per `pm_config`, and steps
a SimulatedPortfolio.

The executor has two phases that share this code:
  - PRIME: full agent-graph invocation per (ticker, day). Fills L2 cache.
  - REPLAY: cached agent outputs only; PM aggregation re-runs per candidate.

`prime=True` runs the LangGraph. `prime=False` reads agent outputs from
`agent_outputs_cache[(ticker, day)]` (a dict prepared by walkforward).
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any

from apps.data.models import DailyBar

from .corporate_actions import actions_on, apply_actions
from .portfolio import SimulatedPortfolio

log = logging.getLogger(__name__)


@dataclass
class SegmentResult:
    dates: list[dt.date] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    cash: list[float] = field(default_factory=list)
    positions_by_day: list[list[dict]] = field(default_factory=list)
    decisions_by_day: list[list[dict]] = field(default_factory=list)
    fills_by_day: list[list[dict]] = field(default_factory=list)
    turnover_total: float = 0.0


def trading_days(start: dt.date, end: dt.date, universe: list[str]) -> list[dt.date]:
    """Distinct dates with bars for any universe ticker (post-as_of filter
    happens at the bar SQL layer)."""
    qs = (
        DailyBar.objects.filter(ticker__in=universe, date__gte=start, date__lte=end)
        .values_list("date", flat=True)
        .distinct()
        .order_by("date")
    )
    return list(qs)


def fill_prices_for(date_: dt.date, universe: list[str]) -> dict[str, float]:
    """Use this day's `open` as the fill price for next-bar execution."""
    rows = DailyBar.objects.filter(ticker__in=universe, date=date_).values("ticker", "open")
    return {r["ticker"]: float(r["open"]) for r in rows}


def close_prices_for(date_: dt.date, universe: list[str]) -> dict[str, float]:
    rows = DailyBar.objects.filter(ticker__in=universe, date=date_).values("ticker", "close")
    return {r["ticker"]: float(r["close"]) for r in rows}


def trailing_returns_for(
    ticker: str, as_of: dt.date, lookback_days: int = 60, price_field: str = "close"
) -> list[float]:
    bars = list(
        DailyBar.objects.filter(ticker=ticker, date__lt=as_of)
        .order_by("-date")
        .values_list(price_field, flat=True)[: lookback_days + 1]
    )
    bars = [float(b) for b in reversed(bars)]
    if len(bars) < 2:
        return []
    return [(bars[i] / bars[i - 1]) - 1.0 for i in range(1, len(bars))]


def spy_regime_scale(
    day: dt.date, *, floor: float = 0.5, benchmark: str = "SPY",
    window: int = 200, price_field: str = "adjusted_close",
) -> float:
    """Deterministic SPY-200dMA regime gate (P7c Part D; research §4.6): 1.0 when
    the benchmark closes above its `window`-day moving average (risk-on), else
    `floor` (de-gross). Point-in-time (strictly before `day`); fail-safe to 1.0
    (full exposure) on insufficient history. Trend-based → orthogonal to vol-
    targeting (a vol-based gate hurt; §4.6)."""
    closes = list(
        DailyBar.objects.filter(ticker=benchmark, date__lt=day)
        .order_by("-date")
        .values_list(price_field, flat=True)[:window]
    )
    if len(closes) < window:
        return 1.0
    vals = [float(c) for c in closes]
    return 1.0 if vals[0] >= sum(vals) / len(vals) else float(floor)


# ---------------------------------------------------------------------------


_NON_PERSONA_KEYS = frozenset(
    {"risk", "valuation", "fundamentals", "technicals", "sentiment", "macro", "news_digest"}
)


def persona_outputs_from_cache(cached: dict, allowed=None) -> dict:
    """Persona votes for one (ticker, day): cached agent outputs minus the
    analytical/risk/valuation keys.

    When `allowed` (the backtest's `personas`) is set, restrict to exactly those
    personas so `Backtest.personas` actually selects the council. Without this,
    non-selected personas still vote: prime always runs the full roster (so they
    are present in the cache) and aggregate_personas falls back to a default
    quality weight of 1.0 for any persona not in the candidate weight vector.
    """
    out = {k: v for k, v in cached.items() if k not in _NON_PERSONA_KEYS}
    if allowed:
        allow = set(allowed)
        out = {k: v for k, v in out.items() if k in allow}
    return out


def run_segment(
    *,
    bt,
    start: dt.date,
    end: dt.date,
    pm_config: dict,
    agent_outputs_cache: dict[tuple[str, dt.date], dict],
    rebalance_dates: set[dt.date] | None = None,
) -> SegmentResult:
    """Replay [start, end] with a fixed `pm_config`, using cached agent outputs.

    `agent_outputs_cache[(ticker, day)]` must contain at least:
        {persona_name: {...PersonaOutput}, "risk": {...}, "valuation": {...}}

    Returns a SegmentResult with the day-by-day equity curve.
    """
    from hedgefund_agents.portfolio.portfolio_manager import aggregate as pm_aggregate

    pf = SimulatedPortfolio(
        starting_cash=float(bt.starting_cash),
        commission_bps=float(bt.commission_bps),
        spread_bps=float(bt.spread_bps),
    )

    universe = list(bt.universe)
    days = trading_days(start, end, universe)
    if not days:
        return SegmentResult()

    out = SegmentResult()
    pending_orders: list[dict] = []  # decisions made yesterday, executed at today's open

    for i, day in enumerate(days):
        # 1. Mark-to-market using prior close
        if i > 0:
            prev = days[i - 1]
            pf.mark_to_market(close_prices_for(prev, universe))
        # 2. Corporate actions (ex-date is `day`)
        for t in universe:
            acts = actions_on(t, day)
            if acts:
                apply_actions(pf, t, acts)
        # 3. Execute pending orders at today's open
        if pending_orders:
            opens = fill_prices_for(day, universe)
            fills = pf.execute(
                pending_orders, opens, as_of=day,
                hold_semantics=getattr(bt, "hold_semantics", "hold_existing"),
            )
            out.fills_by_day.append([f.__dict__ for f in fills])
            pending_orders = []
        else:
            out.fills_by_day.append([])
        # 4. Generate today's decisions (PM aggregation from cached agent outputs).
        #    Only on rebalance days; otherwise carry positions.
        is_rebalance = (rebalance_dates is None) or (day in rebalance_dates)
        decisions_today: list[dict] = []
        if is_rebalance:
            for ticker in universe:
                cached = agent_outputs_cache.get((ticker, day))
                if not cached:
                    continue
                persona_outputs = persona_outputs_from_cache(
                    cached, getattr(bt, "personas", None)
                )
                tr = trailing_returns_for(
                    ticker, day, lookback_days=int(pm_config.get("vol_lookback_days", 60))
                )
                decision = pm_aggregate(
                    ticker=ticker,
                    persona_outputs=persona_outputs,
                    risk=cached.get("risk") or {},
                    valuation=cached.get("valuation") or {},
                    pm_config=pm_config,
                    trailing_returns=tr,
                    portfolio_value=pf.total_value,
                )
                decisions_today.append(decision.model_dump())
            pending_orders = decisions_today
        out.decisions_by_day.append(decisions_today)
        # 5. Snapshot today's portfolio (post any morning fills, marked to prior close)
        out.dates.append(day)
        out.equity.append(pf.total_value)
        out.cash.append(pf.cash)
        out.positions_by_day.append(pf.snapshot())

    out.turnover_total = pf.turnover_total_notional
    return out


# ---------------------------------------------------------------------------
# Deterministic (council-free) sizing — inverse-vol risk parity.
# ---------------------------------------------------------------------------

DETERMINISTIC_DEFAULTS = {
    "vol_target_annual": 0.15,   # book scaled toward this annual vol (down-only, ≤100% gross).
                                 # 0.15 = the macro sleeve's full-investment point: a vt sweep
                                 # (0.10→0.20) saturates here (Sharpe 1.33, +16.7%, 3.8% DD);
                                 # below it the book is under-invested, above it never binds.
    "vol_floor": 0.05,           # floor on per-leg annual vol → bounds runaway inverse-vol weights
    "vol_lookback_days": 60,     # trailing window for realized vol
    "max_leg_weight": 0.40,      # per-leg cap before gross normalization
    "max_gross": 1.0,            # leverage cap. 1.0 = unlevered. >1.0 lets the book lever toward
                                 # vol_target (classic risk parity levers the diversified book to
                                 # equity-like return at low drawdown); execute() borrows to match.
}


def inverse_vol_weights(*, day: dt.date, universe: list[str], config: dict) -> dict[str, float]:
    """Long-only inverse-volatility (risk-parity) target weights for `day`.

    Each leg is sized 1/σ, normalized so Σw=1, per-leg-capped, renormalized, then
    the whole book is scaled toward the vol target (DOWN-only: never levers above
    100% gross). σ is trailing realized vol ending strictly before `day`
    (point-in-time — trailing_returns_for uses date__lt=as_of). Returns {} when
    no leg has enough history. This is a diagonal proxy (no covariance matrix
    exists in the codebase), so it is inverse-vol, not full equal-risk-contribution.
    """
    from hedgefund_agents.portfolio.portfolio_manager import realized_vol_annual

    floor = float(config.get("vol_floor", 0.05))
    lookback = int(config.get("vol_lookback_days", 60))

    # Faithful-to-live mode: reuse the production deterministic constructor so the
    # backtest sizes EXACTLY as apps.portfolios._run_risk_parity_cycle does live
    # (inverse-vol → target_gross, per-sleeve max/min). Use this to validate a live
    # risk_parity strategy. No vol-targeting/leverage here (the live cycle has none).
    if config.get("sizing") == "construct_risk_parity":
        import statistics

        from apps.portfolios.construction import construct_risk_parity
        dvols: dict[str, float] = {}
        for t in universe:
            tr = trailing_returns_for(t, day, lookback_days=lookback)
            if len(tr) >= 20:
                s = statistics.pstdev(tr)  # daily σ, matching the live cycle
                if s > 0:
                    dvols[t] = s
        if not dvols:
            return {}
        res = construct_risk_parity(
            [(t, "") for t in dvols], dvols,
            target_gross_pct=float(config.get("target_gross", 1.0)),
            per_sleeve_max_pct=float(config.get("per_sleeve_max_pct", 0.50)),
            per_sleeve_min_pct=float(config.get("per_sleeve_min_pct", 0.02)),
        )
        weights = dict(res.target_weights)
        # P7c Part D — optional leverage (deploy-faithful with the live RP cycle):
        # scale the unlevered inverse-vol book toward rp_vol_target_annual up to
        # rp_max_gross. Off (vt=0) ⇒ unchanged. port_vol uses the SAME daily σ the
        # live cycle's vols carry, ×√252.
        vt = float(config.get("rp_vol_target_annual", 0.0))
        if vt > 0 and weights:
            # Use the shared max_gross (also the engine's execute gross cap) so the
            # levered book isn't clipped back at execution.
            mg = float(config.get("max_gross", 1.0))
            port_vol = sum(weights[t] * dvols[t] * (252 ** 0.5) for t in weights)
            if port_vol > 0:
                scale = min(mg, vt / port_vol)
                weights = {t: w * scale for t, w in weights.items()}
        return weights

    sigma: dict[str, float] = {}
    for t in universe:
        tr = trailing_returns_for(t, day, lookback_days=lookback)
        if len(tr) >= 20:
            sigma[t] = max(floor, realized_vol_annual(tr))
    if not sigma:
        return {}
    inv = {t: 1.0 / s for t, s in sigma.items()}
    z = sum(inv.values())
    w = {t: v / z for t, v in inv.items()}
    cap = float(config.get("max_leg_weight", 0.40))
    w = {t: min(cap, wi) for t, wi in w.items()}
    z2 = sum(w.values()) or 1.0
    w = {t: wi / z2 for t, wi in w.items()}
    vol_target = float(config.get("vol_target_annual", 0.15))
    max_gross = float(config.get("max_gross", 1.0))
    port_vol = sum(w[t] * sigma[t] for t in w)  # diagonal vol proxy
    # Scale the book toward the vol target, capped at the leverage limit. With
    # max_gross=1.0 this is down-only (unlevered); >1.0 levers up to hit vol_target.
    scale = min(max_gross, vol_target / port_vol) if port_vol > 0 else 1.0
    return {t: wi * scale for t, wi in w.items()}


def _cum_return(returns: list[float]) -> float:
    """Compound a list of period returns into one cumulative total return."""
    c = 1.0
    for r in returns:
        c *= 1.0 + r
    return c - 1.0


def tsmom_weights(*, day: dt.date, universe: list[str], config: dict) -> dict[str, float]:
    """Time-series-momentum (trend) target weights for `day`.

    Per asset: average the SIGN of the trailing cumulative return over each
    ``momentum_lookbacks`` window (default 3/6/12 months). Long when the blended
    sign is positive; flat otherwise (or short when ``allow_short`` — the crisis
    sleeve). Survivors are inverse-vol weighted, gross-normalized, per-leg-capped,
    then the book is scaled toward ``vol_target_annual`` up to ``max_gross``.

    Momentum & vol read ``price_field`` (default ``adjusted_close`` → TOTAL-RETURN
    momentum, so a bond's coupons flip its otherwise-negative price trend — see
    the PR #50 data fix). Returns {} when no asset has enough history. Mirrors
    ``inverse_vol_weights``' sizing tail so risk-parity and trend size identically.
    """
    from hedgefund_agents.portfolio.portfolio_manager import realized_vol_annual

    floor = float(config.get("vol_floor", 0.05))
    vol_lb = int(config.get("vol_lookback_days", 60))
    mom_lbs = [int(x) for x in (config.get("momentum_lookbacks") or [63, 126, 252])]
    allow_short = bool(config.get("allow_short", False))
    price_field = str(config.get("price_field", "adjusted_close"))
    max_lb = max([vol_lb, *mom_lbs])

    sigma: dict[str, float] = {}
    sign: dict[str, float] = {}
    for t in universe:
        tr = trailing_returns_for(t, day, lookback_days=max_lb, price_field=price_field)
        if len(tr) < 20:
            continue
        signs = [1.0 if _cum_return(tr[-lb:]) > 0 else -1.0 for lb in mom_lbs if len(tr) >= lb]
        if not signs:
            continue
        blended = sum(signs) / len(signs)
        s = 1.0 if blended > 0 else (-1.0 if (blended < 0 and allow_short) else 0.0)
        if s == 0.0:
            continue
        sign[t] = s
        vw = tr[-vol_lb:] if len(tr) >= vol_lb else tr
        sigma[t] = max(floor, realized_vol_annual(vw))
    if not sigma:
        return {}
    inv = {t: sign[t] / sigma[t] for t in sigma}
    gross = sum(abs(v) for v in inv.values()) or 1.0
    w = {t: v / gross for t, v in inv.items()}
    cap = float(config.get("max_leg_weight", 0.40))
    w = {t: max(-cap, min(cap, wi)) for t, wi in w.items()}
    z = sum(abs(v) for v in w.values()) or 1.0
    w = {t: wi / z for t, wi in w.items()}
    vol_target = float(config.get("vol_target_annual", 0.15))
    max_gross = float(config.get("max_gross", 1.0))
    port_vol = sum(abs(w[t]) * sigma[t] for t in w)  # diagonal proxy
    scale = min(max_gross, vol_target / port_vol) if port_vol > 0 else 1.0
    return {t: wi * scale for t, wi in w.items()}


def xsec_momentum_weights(*, day: dt.date, universe: list[str], config: dict) -> dict[str, float]:
    """Cross-sectional momentum (sector rotation) target weights for `day`.

    Score each asset by its average trailing cumulative return over
    ``momentum_lookbacks``; hold the top-``top_n`` survivors with a positive-
    momentum gate (long-only), inverse-vol weighted and scaled toward
    ``vol_target_annual`` up to ``max_gross``. Reads ``price_field`` (default
    ``adjusted_close``). Returns {} when nothing clears the momentum>0 gate.
    """
    from hedgefund_agents.portfolio.portfolio_manager import realized_vol_annual

    floor = float(config.get("vol_floor", 0.05))
    vol_lb = int(config.get("vol_lookback_days", 60))
    mom_lbs = [int(x) for x in (config.get("momentum_lookbacks") or [63, 126, 252])]
    top_n = max(1, int(config.get("top_n", 5)))
    price_field = str(config.get("price_field", "adjusted_close"))
    max_lb = max([vol_lb, *mom_lbs])

    scores: dict[str, float] = {}
    sigma: dict[str, float] = {}
    for t in universe:
        tr = trailing_returns_for(t, day, lookback_days=max_lb, price_field=price_field)
        if len(tr) < 20:
            continue
        cums = [_cum_return(tr[-lb:]) for lb in mom_lbs if len(tr) >= lb]
        if not cums:
            continue
        scores[t] = sum(cums) / len(cums)
        vw = tr[-vol_lb:] if len(tr) >= vol_lb else tr
        sigma[t] = max(floor, realized_vol_annual(vw))
    if not scores:
        return {}
    ranked = sorted(scores, key=lambda t: scores[t], reverse=True)
    picks = [t for t in ranked if scores[t] > 0][:top_n]
    if not picks:
        return {}
    inv = {t: 1.0 / sigma[t] for t in picks}
    z = sum(inv.values()) or 1.0
    w = {t: inv[t] / z for t in picks}
    vol_target = float(config.get("vol_target_annual", 0.15))
    max_gross = float(config.get("max_gross", 1.0))
    port_vol = sum(w[t] * sigma[t] for t in w)
    scale = min(max_gross, vol_target / port_vol) if port_vol > 0 else 1.0
    return {t: w[t] * scale for t in w}


# Deterministic sizing dispatch, selected by config["sizing"]. inverse_vol and
# construct_risk_parity both route to inverse_vol_weights (which handles the
# construct_risk_parity sub-mode internally); trend / sector add momentum.
_SIZERS = {
    "inverse_vol": inverse_vol_weights,
    "construct_risk_parity": inverse_vol_weights,
    "tsmom": tsmom_weights,
    "xsec_momentum": xsec_momentum_weights,
}


def run_deterministic_segment(
    *,
    bt,
    start: dt.date,
    end: dt.date,
    config: dict,
    rebalance_dates: set[dt.date] | None = None,
    pf: SimulatedPortfolio | None = None,
) -> SegmentResult:
    """Replay [start, end] with deterministic inverse-vol (risk-parity) sizing.

    No LLM, no council, no agent cache — target weights come purely from trailing
    realized vols. Mirrors run_segment's day loop (mark-to-market, corporate
    actions, next-open execution, snapshot) but replaces the PM vote with
    inverse_vol_weights. The engine's gross cap (SimulatedPortfolio.execute)
    keeps gross ≤ 100%.

    Pass `pf` to continue an existing portfolio across contiguous segments (the
    walk-forward carries one book across OOS folds so it is held continuously
    instead of liquidated-and-rebuilt every fold — the latter inflates turnover
    ~8× and charges phantom round-trip costs a live book never pays).
    """
    if pf is None:
        pf = SimulatedPortfolio(
            starting_cash=float(bt.starting_cash),
            commission_bps=float(bt.commission_bps),
            spread_bps=float(bt.spread_bps),
        )
    universe = list(bt.universe)
    days = trading_days(start, end, universe)
    if not days:
        return SegmentResult()

    out = SegmentResult()
    pending_orders: list[dict] = []
    hold_sem = getattr(bt, "hold_semantics", "hold_existing")
    for i, day in enumerate(days):
        if i > 0:
            pf.mark_to_market(close_prices_for(days[i - 1], universe))
        for t in universe:
            acts = actions_on(t, day)
            if acts:
                apply_actions(pf, t, acts)
        if pending_orders:
            opens = fill_prices_for(day, universe)
            fills = pf.execute(
                pending_orders, opens, as_of=day, hold_semantics=hold_sem,
                max_gross=float(config.get("max_gross", 1.0)),
            )
            out.fills_by_day.append([f.__dict__ for f in fills])
            pending_orders = []
        else:
            out.fills_by_day.append([])
        is_rebalance = (rebalance_dates is None) or (day in rebalance_dates)
        decisions_today: list[dict] = []
        if is_rebalance:
            sizer = _SIZERS.get(config.get("sizing", "inverse_vol"), inverse_vol_weights)
            weights = sizer(day=day, universe=universe, config=config)
            # P7c Part D — deterministic SPY-200dMA regime gate (de-gross in
            # risk-off). Off by default; deploy-faithful with the live cycles.
            if config.get("enable_spy_regime_gate") and weights:
                rs = spy_regime_scale(day, floor=float(config.get("regime_gate_floor", 0.5)))
                if rs != 1.0:
                    weights = {t: w * rs for t, w in weights.items()}
            for t in universe:
                wt = weights.get(t, 0.0)
                decisions_today.append({
                    "ticker": t,
                    "action": "buy" if wt > 0 else "sell",
                    "target_weight_pct": wt * 100.0,
                })
            pending_orders = decisions_today
        out.decisions_by_day.append(decisions_today)
        out.dates.append(day)
        out.equity.append(pf.total_value)
        out.cash.append(pf.cash)
        out.positions_by_day.append(pf.snapshot())

    out.turnover_total = pf.turnover_total_notional
    return out


# ---------------------------------------------------------------------------


def rebalance_dates_for(
    days: list[dt.date], frequency: str
) -> set[dt.date]:
    """Returns the subset of `days` that are rebalance days under `frequency`."""
    if frequency == "daily":
        return set(days)
    if frequency == "weekly":
        # Trade on the first trading day of each ISO week.
        seen: dict[tuple[int, int], dt.date] = {}
        for d in days:
            key = d.isocalendar()[:2]
            if key not in seen:
                seen[key] = d
        return set(seen.values())
    if frequency == "monthly":
        seen: dict[tuple[int, int], dt.date] = {}
        for d in days:
            key = (d.year, d.month)
            if key not in seen:
                seen[key] = d
        return set(seen.values())
    return set(days)


# ---------------------------------------------------------------------------


def prime_agent_cache(
    *,
    bt,
    start: dt.date,
    end: dt.date,
    rebalance_freq: str,
    progress_cb=None,
) -> dict[tuple[str, dt.date], dict]:
    """Walk every (ticker, rebalance day) once with a fresh agent graph
    invocation. Fills L2 cache (LLMResponseCache) and returns an in-memory
    map of agent outputs for the executor.

    Parallelism: ticker-days fan out across a ThreadPoolExecutor (default 8
    threads, override with `settings.BACKTEST_PRIME_PARALLELISM`). graph.invoke
    is IO-bound (LLM HTTP + provider HTTP + small DB writes), so threads scale
    linearly until LLM provider rate limits push back. We use a thread pool
    rather than a Celery chord because (a) each ticker-day is a pure
    function with no orchestration needs, (b) chord overhead for ~3000+
    sub-tasks would be substantial, and (c) keeping the prime in a single
    Celery task leaves the other 7 ForkPoolWorkers free for non-backtest
    work (broker polling, news refreshes, etc.).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from django.conf import settings
    from django.db import close_old_connections

    from apps.data.providers.factory import (
        get_edgar_provider,
        get_fmp_provider,
        get_ownership_provider,
    )
    from apps.graphs.compiler import resolve_graph
    from hedgefund_agents.graphs.council import ANALYTICAL_NODES
    from hedgefund_agents.personas import ALL_PERSONAS
    from hedgefund_agents.versioning import ensure_versions_synced, snapshot_versions

    from .exceptions import BudgetExceeded, ModelUnavailable

    ensure_versions_synced()
    selected_personas = list(bt.personas or ALL_PERSONAS)
    pipeline_agents = ["risk_manager", "portfolio_manager", "cio", "macro", "news_digest"]
    versions = snapshot_versions(
        list(ANALYTICAL_NODES.keys()) + selected_personas + pipeline_agents
    )
    bt.agent_versions = versions
    bt.save(update_fields=["agent_versions"])

    # LangGraph compiled graphs are stateless under invoke(); shared across threads.
    # P4c: resolve from bt.graph_version when ENABLE_DB_GRAPHS is on; else fall
    # back to the hardcoded council with bt.personas. disable_cio still flows via
    # state (below), unchanged — the compiler takes no disable_cio argument.
    graph = resolve_graph(bt)
    # FMP / EDGAR providers wrap an httpx.Client; httpx Client is thread-safe.
    data_provider = get_fmp_provider(user=bt.user)
    filings_provider = get_edgar_provider()
    ownership_provider = get_ownership_provider(user=bt.user)

    universe = list(bt.universe)
    days = trading_days(start, end, universe)
    rebal = sorted(rebalance_dates_for(days, rebalance_freq))

    work_items = [(ticker, day) for day in rebal for ticker in universe]
    total = len(work_items)
    budget_cap = float(getattr(bt, "max_budget_usd", 0) or 0)
    max_workers = int(getattr(settings, "BACKTEST_PRIME_PARALLELISM", 8))

    cache_map: dict[tuple[str, dt.date], dict] = {}
    done = 0
    failures = 0
    budget_exceeded: BudgetExceeded | None = None
    # First ModelUnavailable wins: subsequent calls to the same model would
    # raise the same way, so we abort the entire run rather than grinding
    # through 3000+ guaranteed-failing invocations.
    model_unavailable: ModelUnavailable | None = None

    def _unwrap_model_unavailable(err: BaseException) -> ModelUnavailable | None:
        # graph.invoke may wrap our adapter exception in LangGraph's own error
        # type (or any agent-layer wrapper). Walk the cause/context chain so we
        # catch the underlying ModelUnavailable regardless of how it's nested.
        cur: BaseException | None = err
        seen: set[int] = set()
        while cur is not None and id(cur) not in seen:
            if isinstance(cur, ModelUnavailable):
                return cur
            seen.add(id(cur))
            cur = cur.__cause__ or cur.__context__
        return None

    def _invoke_one(
        ticker: str, day: dt.date,
    ) -> tuple[str, dt.date, dict | None, Exception | None]:
        # Each thread gets its own DB connection — recycle on entry so we don't
        # accumulate idle conns past Postgres' max_connections.
        close_old_connections()
        initial_state: dict[str, Any] = {
            "ticker": ticker,
            "as_of_date": day,
            "model_overrides": bt.model_overrides or {},
            "data_provider": data_provider,
            "filings_provider": filings_provider,
            "ownership_provider": ownership_provider,
            "use_llm_cache": True,
            "backtest_id": bt.id,
            "agent_versions": versions,
            # CIO is a discretionary veto layer. Default-off in backtests for
            # reproducibility; per-backtest opt-in via Backtest.disable_cio.
            "disable_cio": bool(getattr(bt, "disable_cio", True)),
        }
        try:
            final = graph.invoke(initial_state)
        except Exception as e:
            return ticker, day, None, e
        entry: dict[str, Any] = {}
        for k in selected_personas:
            if final.get(k):
                entry[k] = final[k]
        for k in ("risk", "valuation", "fundamentals", "technicals", "sentiment",
                  "macro", "news_digest"):
            if final.get(k):
                entry[k] = final[k]
        return ticker, day, entry, None

    def _ingest(ticker: str, day: dt.date, entry: dict | None, err: Exception | None) -> bool:
        """Handle one completed unit. Returns True if the run should stop
        dispatching new work (budget exceeded OR a model is permanently
        unavailable — see ModelUnavailable in apps/backtests/exceptions)."""
        nonlocal done, failures, budget_exceeded, model_unavailable
        done += 1
        # ModelUnavailable: every subsequent call to the same model would fail
        # the same way (HTTP 402 out-of-credits, 401 bad key, 404 wrong slug,
        # 403 access denied). Stop the run *now* with a clear error rather
        # than count it as a normal ticker-day failure and grind through
        # thousands more guaranteed-doomed invocations.
        unwrapped = _unwrap_model_unavailable(err) if err is not None else None
        if unwrapped is not None:
            model_unavailable = unwrapped
            failures += 1
            log.error(
                "graph.invoke for %s %s hit a permanent model error; aborting prime: %s",
                ticker, day, unwrapped,
            )
            if progress_cb:
                progress_cb(done, total, f"aborted on unavailable model: {unwrapped.model}")
            return True
        if err is not None:
            log.warning("graph.invoke failed for %s %s: %s", ticker, day, err)
            failures += 1
        elif entry is not None:
            cache_map[(ticker, day)] = entry
        if progress_cb:
            note = (
                f"primed {ticker} {day.isoformat()}"
                if err is None
                else f"failed {ticker} {day.isoformat()}"
            )
            progress_cb(done, total, note)
        if budget_cap > 0:
            spent = float(
                type(bt).objects.filter(pk=bt.pk)
                .values_list("total_cost_usd", flat=True).first() or 0
            )
            if spent >= budget_cap:
                budget_exceeded = BudgetExceeded(spent, budget_cap, done, total)
                return True
        return False

    if max_workers <= 1:
        # Serial path — used by tests that need deterministic single-connection
        # DB semantics, and by anyone explicitly disabling parallelism. Avoids
        # the ThreadPoolExecutor entirely so the budget check sees writes from
        # the same connection that issued them.
        for t, d in work_items:
            close_old_connections()
            _ticker, _day, _entry, _err = _invoke_one(t, d)
            if _ingest(_ticker, _day, _entry, _err):
                break
    else:
        # Parallel path — IO-bound graph.invoke calls fan out across threads.
        # Budget kill-switch over-shoots by up to (max_workers - 1) already-
        # running futures; that's acceptable given the order-of-magnitude
        # wall-time speed-up.
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="prime") as pool:
            futures = {pool.submit(_invoke_one, t, d): (t, d) for t, d in work_items}
            for fut in as_completed(futures):
                ticker, day, entry, err = fut.result()
                if _ingest(ticker, day, entry, err):
                    for pending in futures:
                        pending.cancel()
                    break

    if model_unavailable is not None:
        # Surface the upstream provider error verbatim so the user sees the
        # actual "Out of credits" / "Invalid API key" / etc. text without
        # having to scrape worker logs.
        raise model_unavailable
    if budget_exceeded is not None:
        raise budget_exceeded

    # Quality gate: persist the actual completeness ratio so the UI can show
    # whether a run is complete or partial, and abort below the configured
    # `prime_min_completeness` (default 0.85). Failures inside this band still
    # taint metrics, so the run is marked ABORTED_PARTIAL, not DONE.
    completeness = (1.0 - failures / total) if total else 1.0
    min_required = float(getattr(bt, "prime_min_completeness", 0.85) or 0.85)
    bt.prime_completeness = completeness
    bt.save(update_fields=["prime_completeness"])
    if completeness < min_required:
        from .exceptions import SparseCache
        raise SparseCache(completeness, min_required, done, total)
    if failures:
        log.warning(
            "prime_agent_cache: %d/%d graph invocations failed (completeness=%.2f)",
            failures, total, completeness,
        )
    return cache_map
