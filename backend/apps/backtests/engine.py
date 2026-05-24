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
    ticker: str, as_of: dt.date, lookback_days: int = 60
) -> list[float]:
    bars = list(
        DailyBar.objects.filter(ticker=ticker, date__lt=as_of)
        .order_by("-date")
        .values_list("close", flat=True)[: lookback_days + 1]
    )
    bars = [float(b) for b in reversed(bars)]
    if len(bars) < 2:
        return []
    return [(bars[i] / bars[i - 1]) - 1.0 for i in range(1, len(bars))]


# ---------------------------------------------------------------------------


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
                persona_outputs = {
                    k: v for k, v in cached.items()
                    if k not in ("risk", "valuation", "fundamentals", "technicals",
                                 "sentiment", "macro", "news_digest")
                }
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

    from apps.data.providers.factory import get_edgar_provider, get_fmp_provider
    from hedgefund_agents.graphs.council import (
        ANALYTICAL_NODES,
        build_council_graph,
    )
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
    graph = build_council_graph(personas=selected_personas)
    # FMP / EDGAR providers wrap an httpx.Client; httpx Client is thread-safe.
    data_provider = get_fmp_provider(user=bt.user)
    filings_provider = get_edgar_provider()

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
