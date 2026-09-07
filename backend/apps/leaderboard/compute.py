"""Nightly leaderboard recompute (P3b) — pure Python, no LLM calls.

Agents: per-persona signal accuracy (hit-rate + Brier vs the forward N-day
return) plus a PnL column folded in from backtest per-agent attribution.
Strategies: rolling Sharpe/Sortino/drawdown/hit-rate/turnover/$-per-cycle over
**disjoint** holding intervals (see ``period_returns``), plus per-flavor median
aggregates.

Statistical honesty (wave 3, WP P1)
-----------------------------------
* Strategy return series come from DISJOINT intervals (cycle N -> cycle N+1),
  never from chained cumulative-since-inception windows.
* Ratios annualise by the OBSERVED cadence (median gap between cycles), not a
  hard-coded 252.
* Below ``MIN_CYCLES`` observations a row is ``provisional`` and its
  ``sharpe`` / ``sortino`` / ``annualised_return_pct`` are ``None`` — a Sharpe
  from 13 points is not a Sharpe. ``n_cycles``, ``total_return_pct``,
  ``hit_rate`` and ``max_drawdown_pct`` survive (they are meaningful at small n).
* Sortino is bounded: fewer than ``MIN_NEGATIVE_OBS`` negative periods, or a
  zero downside deviation, yields ``None`` + a ``sortino_note``; anything past
  ``SORTINO_CAP`` is capped and says so.
* Agent decisions are deduped (one per agent/version/model/ticker/as_of — 30
  re-runs of one call are one observation) and thinned to NON-OVERLAPPING
  forward windows before any rate or mean is taken.
* Every decimal written is clamped to what its column can hold, so an extreme
  input can never abort the nightly recompute mid-flight.
"""
from __future__ import annotations

import datetime as dt
import math
import statistics
from decimal import ROUND_HALF_UP, Decimal

from django.db import models
from django.db.models import Avg, Count, Sum
from django.utils import timezone

from apps.backtests.metrics import drawdown_pct, sharpe_ratio, sortino_ratio
from apps.backtests.metrics import hit_rate as hit_rate_fn
from apps.backtests.models import BacktestMetrics
from apps.models_catalog.presets import ANALYTICAL_AGENTS, PERSONA_AGENTS
from apps.portfolios.cycle_mark import ensure_baseline_snapshot, ensure_cycle_snapshot
from apps.portfolios.models import PortfolioStrategy, PortfolioTarget
from apps.runs.models import AgentMessage, Run
from hedgefund_agents.models import LLMCall

from .council_alpha import BASELINE_VERSION
from .forward_returns import DEFAULT_FORWARD_DAYS, batch_forward_returns, brier, wilson_interval
from .models import METRICS_VERSION, AgentScorecard, ModelScorecard, StrategyScorecard
from .period_returns import (
    DAYS_PER_YEAR,
    Period,
    PriceBook,
    elapsed_days,
    period_returns,
    periods_per_year,
    price_book_for,
)

WINDOWS_AGENT = {"30d": 30, "90d": 90, "lifetime": None}
WINDOWS_STRATEGY = {"30d": 30, "90d": 90, "ytd": "ytd", "lifetime": None}
MIN_DECISIONS = 30
MIN_CYCLES = 20
# Council-alpha needs more paired observations than the generic provisional
# threshold before it's worth quoting (plan acceptance criterion: ≥30 days of
# baseline). Below this, council_alpha_bps stays null and the UI shows "needs
# 30 days of baseline".
MIN_COUNCIL_ALPHA_CYCLES = 30
# A Sortino needs a downside to divide by. With fewer than this many negative
# periods the denominator is an artefact of the sample, not a risk estimate —
# production showed Sortino 911.38 from 13 overlapping points.
MIN_NEGATIVE_OBS = 3
# Hard ceilings so no ratio is ever presentable as a plausible-looking number.
SORTINO_CAP = 20.0
SHARPE_CAP = 20.0
# Fallback cadence when a strategy has cycles but no measurable gaps.
ANNUALIZE_CYCLES = 252


# ---- small helpers ----

def _dec4(v):
    return Decimal(str(round(v, 4))) if v is not None else None


def _dec2(v):
    return Decimal(str(round(v, 2))) if v is not None else None


def _dec6(v):
    return Decimal(str(round(v, 6))) if v is not None else None


_FIELD_BOUNDS: dict[tuple[str, str], tuple[Decimal, Decimal]] = {}


def _bounds_for(model, name: str) -> tuple[Decimal, Decimal] | None:
    key = (model.__name__, name)
    if key not in _FIELD_BOUNDS:
        try:
            field = model._meta.get_field(name)
        except Exception:  # pragma: no cover — defensive
            return None
        if not isinstance(field, models.DecimalField):
            _FIELD_BOUNDS[key] = None  # type: ignore[assignment]
        else:
            step = Decimal(1).scaleb(-field.decimal_places)
            cap = Decimal(10) ** (field.max_digits - field.decimal_places) - step
            _FIELD_BOUNDS[key] = (step, cap)
    return _FIELD_BOUNDS[key]


def _fit_decimals(model, values: dict) -> dict:
    """Quantize + clamp every DecimalField value to what its column can hold.

    The review's F-overflow finding: a perfectly ordinary 6% mean "cycle return"
    annualised to 2.38e8 %, which does not fit ``NUMERIC(12,4)``. On Postgres the
    INSERT raised ``numeric field overflow`` — *after* ``recompute_strategies``
    had already deleted the day's rows, so the nightly task left the leaderboard
    empty; on the read side Django's converter raised ``InvalidOperation`` and
    every leaderboard GET 500'd. Clamping at the write boundary makes that class
    of failure impossible for every column at once.
    """
    out = dict(values)
    for name, raw in values.items():
        if raw is None:
            continue
        bounds = _bounds_for(model, name)
        if not bounds:
            continue
        step, cap = bounds
        try:
            dec = Decimal(str(raw)).quantize(step, rounding=ROUND_HALF_UP)
        except Exception:  # pragma: no cover — non-numeric value
            continue
        out[name] = max(-cap, min(cap, dec))
    return out


def _cutoff(window_days, today: dt.date):
    if window_days is None:
        return None
    if window_days == "ytd":
        return dt.date(today.year, 1, 1)
    return today - dt.timedelta(days=window_days)


def _role_for(agent: str) -> str:
    if agent in PERSONA_AGENTS:
        return "persona"
    if agent in ANALYTICAL_AGENTS:
        return "analytical"
    if agent in {"portfolio_manager", "pm_decision"}:
        return "pm"
    if agent in {"risk", "risk_manager"}:
        return "risk"
    if agent == "cio":
        return "cio"
    if agent == "macro":
        return "macro"
    if agent == "news_digest":
        return "news"
    return agent


# ---- ratio primitives (cadence-aware, bounded) ----

def _sharpe(rets: list[float], ppy: float) -> float | None:
    """Annualised Sharpe from a DISJOINT return series. None when undefined."""
    if len(rets) < 2:
        return None
    sd = statistics.pstdev(rets)
    if sd <= 0:
        return None
    value = statistics.fmean(rets) / sd * math.sqrt(ppy)
    if not math.isfinite(value):
        return None
    return max(-SHARPE_CAP, min(SHARPE_CAP, value))


def _sortino(rets: list[float], ppy: float) -> tuple[float | None, str]:
    """Annualised Sortino, or ``(None, why)``.

    Zero (or near-zero) downside deviation is the failure mode that produced
    911.38 in production: with 13 overlapping, almost-always-positive points the
    denominator collapses and the ratio explodes. A Sortino is only quoted when
    there is a real downside to divide by.
    """
    if len(rets) < 2:
        return None, "not enough observations"
    negatives = [r for r in rets if r < 0]
    if len(negatives) < MIN_NEGATIVE_OBS:
        return None, f"fewer than {MIN_NEGATIVE_OBS} negative periods"
    downside = math.sqrt(statistics.fmean([min(0.0, r) ** 2 for r in rets]))
    if downside <= 0:
        return None, "no measurable downside deviation"
    value = statistics.fmean(rets) / downside * math.sqrt(ppy)
    if not math.isfinite(value):
        return None, "not finite"
    if abs(value) > SORTINO_CAP:
        return math.copysign(SORTINO_CAP, value), f"capped at ±{SORTINO_CAP:g}"
    return value, ""


# ---- agents ----

def _agent_pnl_bps(agent: str, model_id: str) -> float | None:
    """Mean per-agent PnL contribution (bps of starting NAV) across backtests
    whose attribution covers this agent **on this model**.

    Wave 3: the old version keyed on the agent alone, so one persona's number
    was repeated verbatim on every model row it appeared under (production
    showed lynch = −29.69 bps on four different models — a per-strategy constant
    masquerading as per-model attribution). A backtest records the model it ran
    each agent under in ``Backtest.model_overrides``; only matching runs may be
    attributed to a model row. No matching backtest => ``None``, not a borrowed
    number.
    """
    vals = []
    qs = BacktestMetrics.objects.exclude(per_agent_attribution={}).select_related("backtest")
    for m in qs.iterator():
        attr = m.per_agent_attribution or {}
        if agent not in attr:
            continue
        backtest = m.backtest
        if backtest is None:
            continue
        bt_model = (getattr(backtest, "model_overrides", None) or {}).get(agent, "")
        if (bt_model or "") != (model_id or ""):
            continue
        try:
            dollars = float(attr[agent])
            cash = float(backtest.starting_cash or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if cash > 0:
            vals.append(dollars / cash * 10000.0)
    return statistics.fmean(vals) if vals else None


def _thin_to_disjoint(rows: list[dict], forward_days: int) -> list[dict]:
    """Keep only decisions whose forward windows do NOT overlap.

    Two calls on the same ticker three days apart are scored over two
    5-trading-day windows that share four of their days: they are one
    observation of the market dressed up as two. Greedily keep the earliest
    decision per (agent, version, model, ticker) and skip any later one whose
    window would still be open. Different tickers never conflict.
    """
    if forward_days <= 0:
        return rows
    # ~7 calendar days per 5 trading days.
    span = dt.timedelta(days=max(1, math.ceil(forward_days * 7 / 5)))
    last_kept: dict[tuple, dt.date] = {}
    out: list[dict] = []
    for row in sorted(rows, key=lambda r: (r["as_of_date"], r["run_id"])):
        key = (
            row["user_id"], row["agent"], row["version"], row["model"], row["ticker"],
        )
        previous = last_kept.get(key)
        if previous is not None and row["as_of_date"] < previous + span:
            continue
        last_kept[key] = row["as_of_date"]
        out.append(row)
    return out


def _collect_agent_decisions(
    cutoff, forward_days: int, *, user=None
) -> list[dict]:
    """Persona decisions to score, deduped and thinned to disjoint windows.

    Windowing is on ``run.as_of_date`` (the date the call is ABOUT), not
    ``run.created_at``: a run backdated years via the user-settable
    ``as_of_date`` used to land in the 30d window and be graded against a
    forward return its model may well have memorised (F-hindsight). It is now
    scored in the window it actually belongs to.

    Deduplication: one decision per (user, agent, version, model, ticker,
    as_of_date), keeping the FIRST run. Re-running the same ticker/date thirty
    times is one observation, not thirty (F-rerun-gaming: the Wilson CI used to
    narrow and ``provisional`` flipped off from a single real call).
    """
    qs = AgentMessage.objects.filter(
        agent_name__in=PERSONA_AGENTS, run__status=Run.DONE
    ).select_related("run")
    if cutoff:
        qs = qs.filter(run__as_of_date__gte=cutoff)
    if user is not None:
        qs = qs.filter(run__user=user)

    raw: list[dict] = []
    for m in qs.iterator():
        run = m.run
        if not run.tickers:
            continue
        po = m.parsed_output or {}
        sig = po.get("signal")
        if sig not in ("bullish", "neutral", "bearish"):
            continue
        raw.append({
            "run_id": run.id,
            "user_id": run.user_id,
            "ticker": str(run.tickers[0]).upper(),
            "as_of_date": run.as_of_date,
            "signal": sig,
            "agent": m.agent_name,
            "version": (run.agent_versions or {}).get(m.agent_name, ""),
            "model": (run.model_overrides or {}).get(m.agent_name, ""),
            "conf": int(po.get("confidence", 0) or 0),
            "directional": sig in ("bullish", "bearish"),
        })
    if not raw:
        return []

    # Consensus is read from the FULL set (before dedup) so a persona's
    # "contrarian" status still reflects the council it actually sat on.
    _annotate_contrarian(raw)

    deduped: dict[tuple, dict] = {}
    for row in sorted(raw, key=lambda r: (r["run_id"],)):
        key = (row["user_id"], row["agent"], row["version"], row["model"],
               row["ticker"], row["as_of_date"])
        deduped.setdefault(key, row)
    rows = _thin_to_disjoint(list(deduped.values()), forward_days)

    # One query for every forward return (F-n+1).
    returns = batch_forward_returns(
        [(r["ticker"], r["as_of_date"]) for r in rows], forward_days
    )
    out: list[dict] = []
    for row in rows:
        ret = returns.get((row["ticker"], row["as_of_date"]))
        if ret is None:
            continue
        hit = None
        signed = 0.0
        if row["directional"]:
            sig = row["signal"]
            hit = (sig == "bullish" and ret > 0) or (sig == "bearish" and ret < 0)
            signed = ret if sig == "bullish" else -ret
        out.append({**row, "ret": signed, "hit": hit})
    out.sort(key=lambda r: (r["as_of_date"], r["run_id"]))
    return out


def _annotate_contrarian(decisions: list[dict]) -> None:
    """Tag each decision with ``contrarian`` = directional AND its signal differs
    from the run's majority-persona consensus. Mutates in place. Consensus is the
    plurality persona signal among that run's decisions (ties resolve to whichever
    Counter surfaces first — a tie means no clear consensus to be contrarian to)."""
    import collections

    by_run: dict[int, list[dict]] = collections.defaultdict(list)
    for d in decisions:
        by_run[d["run_id"]].append(d)
    consensus: dict[int, str] = {}
    for run_id, rows in by_run.items():
        counts = collections.Counter(r["signal"] for r in rows)
        consensus[run_id] = counts.most_common(1)[0][0]
    for d in decisions:
        d["contrarian"] = bool(d["directional"]) and d["signal"] != consensus.get(d["run_id"])


def recompute_agents(today: dt.date, forward_days: int = DEFAULT_FORWARD_DAYS) -> None:
    AgentScorecard.objects.filter(as_of=today).delete()
    pnl_cache: dict[tuple[str, str], float | None] = {}
    for window, days in WINDOWS_AGENT.items():
        decisions = _collect_agent_decisions(_cutoff(days, today), forward_days)
        groups: dict[tuple, list] = {}
        for d in decisions:
            groups.setdefault(
                (d["user_id"], d["agent"], d["version"], d["model"]), []
            ).append(d)
        for (user_id, agent, version, model), rows in groups.items():
            pnl_key = (agent, model)
            if pnl_key not in pnl_cache:
                pnl_cache[pnl_key] = _agent_pnl_bps(agent, model)
            directional = [r for r in rows if r["directional"]]
            nd = len(directional)
            hits = sum(1 for r in directional if r["hit"])
            hit_rate = hits / nd if nd else None
            ci_low, ci_high = wilson_interval(hits, nd) if nd else (None, None)
            brier_vals = [brier(r["conf"] / 100.0, 1 if r["hit"] else 0) for r in directional]
            brier_score = statistics.fmean(brier_vals) if brier_vals else None
            avg_ret_bps = (
                statistics.fmean([r["ret"] for r in directional]) * 10000 if directional else None
            )
            # Disagreement value: of the directional calls that went against the
            # run consensus, how many were right? (Wilson CI for honesty.)
            contrarian = [r for r in directional if r["contrarian"]]
            nc = len(contrarian)
            c_hits = sum(1 for r in contrarian if r["hit"])
            c_hit_rate = c_hits / nc if nc else None
            c_ci_low, c_ci_high = wilson_interval(c_hits, nc) if nc else (None, None)
            AgentScorecard.objects.create(**_fit_decimals(AgentScorecard, {
                "user_id": user_id,
                "agent_name": agent, "agent_version": version, "model_id": model,
                "window": window, "as_of": today,
                "n_decisions": len(rows), "n_directional": nd,
                "hit_rate": hit_rate, "hit_rate_ci_low": ci_low,
                "hit_rate_ci_high": ci_high, "brier_score": brier_score,
                "avg_forward_return_bps": avg_ret_bps,
                "pnl_contribution_bps": pnl_cache[pnl_key],
                "n_contrarian_decisions": nc,
                "contrarian_hit_rate": c_hit_rate,
                "contrarian_hit_rate_ci_low": c_ci_low,
                "contrarian_hit_rate_ci_high": c_ci_high,
                "provisional": nd < MIN_DECISIONS,
                "metrics_version": METRICS_VERSION,
            }))


# ---- models ----

def recompute_models(today: dt.date, forward_days: int = DEFAULT_FORWARD_DAYS) -> None:
    ModelScorecard.objects.filter(as_of=today).delete()
    for window, days in WINDOWS_AGENT.items():
        cutoff = _cutoff(days, today)
        call_qs = LLMCall.objects.filter(cost_usd__gte=0)
        if cutoff:
            call_qs = call_qs.filter(created_at__date__gte=cutoff)
        agg: dict[tuple, dict] = {}
        for c in call_qs.values("model", "agent_name").annotate(
            n=Count("id"), avg_cost=Avg("cost_usd")
        ):
            key = (c["model"] or "(default)", _role_for(c["agent_name"]))
            a = agg.setdefault(key, {"n": 0, "cost_sum": 0.0})
            a["n"] += c["n"]
            a["cost_sum"] += float(c["avg_cost"] or 0) * c["n"]

        ret_by_model: dict[str, list[float]] = {}
        for d in _collect_agent_decisions(cutoff, forward_days):
            if d["directional"]:
                ret_by_model.setdefault(d["model"] or "(default)", []).append(d["ret"])

        for (model, role), a in agg.items():
            n = a["n"]
            avg_cost = a["cost_sum"] / n if n else None
            avg_ret_bps = None
            cost_adj = None
            if role == "persona" and ret_by_model.get(model):
                avg_ret_bps = statistics.fmean(ret_by_model[model]) * 10000
                if avg_cost and avg_cost > 0:
                    cost_adj = avg_ret_bps / avg_cost
            ModelScorecard.objects.create(**_fit_decimals(ModelScorecard, {
                "model_id": model, "agent_role": role, "window": window, "as_of": today,
                "n_decisions": n, "avg_cost_per_decision_usd": avg_cost,
                "avg_forward_return_bps": avg_ret_bps,
                "cost_adjusted_return_bps": cost_adj,
                "provisional": n < MIN_DECISIONS,
            }))


# ---- strategies ----

def _avg_turnover(targets) -> float | None:
    """Mean per-cycle one-way turnover (fraction of NAV) from target_weight
    deltas between consecutive cycles. ``target_weights`` are SIGNED FRACTIONS
    of NAV (``cycle_mark`` multiplies them by 100 to get percentage points), so
    the one-way turnover is ``Σ|Δw| / 2`` — no extra /100."""
    weights = [t.target_weights or {} for t in targets]
    if len(weights) < 2:
        return None
    turns = []
    for prev, cur in zip(weights, weights[1:], strict=False):
        keys = set(prev) | set(cur)
        delta = sum(abs(float(cur.get(k, 0)) - float(prev.get(k, 0))) for k in keys)
        turns.append(delta / 2.0)
    return statistics.fmean(turns) if turns else None


def _metrics_from_periods(periods: list[Period]) -> dict | None:
    """Sharpe/Sortino/max-DD/hit-rate/CAGR from a DISJOINT return series.

    Returns ``None`` only when there is nothing at all to report. Below
    ``MIN_CYCLES`` observations the row is ``provisional`` and every ratio is
    ``None`` — the sample cannot support one.
    """
    if not periods:
        return None
    rets = [p.ret for p in periods]
    equity = [1.0]
    for r in rets:
        equity.append(equity[-1] * (1 + r))
    n = len(rets)
    ppy = periods_per_year(periods) or float(ANNUALIZE_CYCLES)
    span_days = elapsed_days(periods)
    provisional = n < MIN_CYCLES

    total = equity[-1] - 1.0
    out = {
        "n": n,
        "ppy": ppy,
        "span_days": span_days,
        "total": total,
        "dd": drawdown_pct(equity),
        "hit": hit_rate_fn(rets),
        "sharpe": None,
        "sortino": None,
        "sortino_note": "",
        "ann": None,
        "provisional": provisional,
    }
    if provisional:
        out["sortino_note"] = f"fewer than {MIN_CYCLES} observations"
        return out

    out["sharpe"] = _sharpe(rets, ppy)
    out["sortino"], out["sortino_note"] = _sortino(rets, ppy)
    # CAGR over the calendar time the series actually covers — not
    # ``(1 + mean) ** 252``, which turned a 6% mean into 2.38e8 %.
    if span_days > 0 and total > -1.0:
        try:
            out["ann"] = (1.0 + total) ** (DAYS_PER_YEAR / span_days) - 1.0
        except (OverflowError, ValueError):  # pragma: no cover — defensive
            out["ann"] = None
    return out



def _strategy_nav(portfolio, prices: PriceBook | None = None) -> float:
    """Book EQUITY (cash + marked positions), not the cash balance.

    ``council_net_value_usd`` used to multiply the window's excess return by
    ``portfolio.cash_balance``. For an invested book that is the small residual
    left over after buying, so a fully-invested $100k book with $10k of cash
    reported a tenth of the dollars the council actually produced (or
    destroyed). Positions are marked at their latest daily close and fall back
    to ``avg_cost`` when no bar is cached.
    """
    if portfolio is None:
        return 0.0
    cash = float(getattr(portfolio, "cash_balance", 0) or 0)
    positions = list(portfolio.positions.all())
    if not positions:
        return cash
    if prices is None:
        prices = PriceBook(
            [p.ticker for p in positions],
            timezone.localdate() - dt.timedelta(days=365),
            timezone.localdate(),
        )
    today = timezone.localdate()
    equity = cash
    for pos in positions:
        try:
            qty = float(pos.quantity or 0)
            cost = float(pos.avg_cost or 0)
        except (TypeError, ValueError):  # pragma: no cover — defensive
            continue
        px = prices.close_on_or_before(pos.ticker, today)
        equity += qty * (px if px is not None else cost)
    return equity


def _metrics_from_returns(rets: list[float]) -> dict | None:
    """Legacy per-cycle metric bundle, kept ONLY for the council-alpha path.

    ``rets`` here are the paired realised/baseline ``since_as_of_pct`` values,
    whose overlap largely cancels in the realised−baseline difference. Strategy
    scorecards no longer go through here — see ``_metrics_from_periods``.
    """
    if len(rets) < 2:
        return None
    equity = [1.0]
    for r in rets:
        equity.append(equity[-1] * (1 + r))
    mean_r = statistics.fmean(rets)
    ann = (1 + mean_r) ** ANNUALIZE_CYCLES - 1 if mean_r > -1 else -1.0
    return {
        "total": equity[-1] - 1.0,
        "sharpe": sharpe_ratio(rets),
        "sortino": sortino_ratio(rets),
        "dd": drawdown_pct(equity),
        "hit": hit_rate_fn(rets),
        "ann": ann,
    }


def _pair_is_same_pass(t) -> bool:
    """True when realised + baseline snapshots were stamped within the same
    marking pass (≤ the cycle-mark freshness window apart) — i.e. against the
    same bar availability — so their diff is a real signal, not timing noise."""
    from apps.portfolios.cycle_mark import SNAPSHOT_FRESH_FOR_SECONDS

    rs = (t.marked_snapshot or {}).get("snapshot_at")
    bs = (t.baseline_marked_snapshot or {}).get("snapshot_at")
    if not rs or not bs:
        return False
    try:
        r_at = dt.datetime.fromisoformat(rs)
        b_at = dt.datetime.fromisoformat(bs)
    except (TypeError, ValueError):
        return False
    return abs((r_at - b_at).total_seconds()) <= SNAPSHOT_FRESH_FOR_SECONDS


def _paired_returns(targets) -> tuple[list[float], list[float]]:
    """Realised vs council-free-baseline per-cycle returns, over the cycles
    that captured a baseline (council-alpha is forward-only).

    P10 §E1: a pair is only read AS-IS when both snapshots were stamped in the
    same marking pass; otherwise BOTH are force-refreshed together so they are
    marked against identical bar availability. The old code read
    ``t.marked_snapshot`` as-is (possibly stamped days earlier by a page view)
    while recomputing only the baseline — on byte-identical books that produced
    +31bp of phantom "overlay alpha" (MU priced from two different dates). The
    regression test asserts alpha ≡ 0 for identical books.
    """
    realised: list[float] = []
    baseline: list[float] = []
    for t in targets:
        if not (t.baseline_weights or {}):
            continue
        if _pair_is_same_pass(t):
            rv = (t.marked_snapshot or {}).get("since_as_of_pct")
            bv = (t.baseline_marked_snapshot or {}).get("since_as_of_pct")
        else:
            rv = (ensure_cycle_snapshot(t, force=True) or {}).get("since_as_of_pct")
            bv = (ensure_baseline_snapshot(t, force=True) or {}).get("since_as_of_pct")
        if rv in (None, "", "None") or bv in (None, "", "None"):
            continue
        try:
            realised.append(float(rv) / 100.0)
            baseline.append(float(bv) / 100.0)
        except (TypeError, ValueError):
            continue
    return realised, baseline


def _council_alpha(targets, nav: float, council_cost: float) -> dict:
    """Council-alpha for a strategy/window: annualised (realised − baseline) in
    bps, plus the council's net dollar value and cumulative cost.

    Alpha/value stay null until ≥ MIN_COUNCIL_ALPHA_CYCLES paired cycles exist
    (short windows are too noisy — plan risk #6). ``cost_usd`` is always
    surfaced so the UI can show "cost $Y" even before alpha is meaningful.

    ``nav`` is now book EQUITY (see ``_strategy_nav``); it used to be the cash
    balance, which understated the council's dollar value by the invested
    fraction of the book.
    """
    out = {"alpha_bps": None, "net_value_usd": None, "cost_usd": council_cost}
    realised, baseline = _paired_returns(targets)
    if len(realised) < MIN_COUNCIL_ALPHA_CYCLES:
        return out
    mr = _metrics_from_returns(realised)
    mb = _metrics_from_returns(baseline)
    if not mr or not mb:
        return out
    out["alpha_bps"] = (mr["ann"] - mb["ann"]) * 10000.0
    # Dollar value the council produced (or destroyed): gross alpha on the
    # window × NAV, net of the council's LLM spend. (plan §"cost-vs-benefit")
    out["net_value_usd"] = (mr["total"] - mb["total"]) * nav - council_cost
    return out


def recompute_strategies(today: dt.date) -> None:
    StrategyScorecard.objects.filter(as_of=today).delete()
    by_flavor: dict[tuple, list] = {}
    # P10 §D1: skip archived strategies — no nightly scorecard churn for dead
    # experiments (their historical scorecard rows remain queryable).
    for s in (
        PortfolioStrategy.objects.filter(is_active=True)
        .select_related("portfolio")
        .iterator()
    ):
        all_targets = list(
            PortfolioTarget.objects.filter(strategy=s, status=PortfolioTarget.DONE)
            .order_by("as_of_date")
        )
        # One price query per strategy, shared by every window and by the
        # council-alpha legs.
        prices = price_book_for(all_targets, today, "target_weights", "baseline_weights")
        nav = _strategy_nav(getattr(s, "portfolio", None))
        for window, days in WINDOWS_STRATEGY.items():
            cutoff = _cutoff(days, today)
            targets = (
                [t for t in all_targets if t.as_of_date >= cutoff] if cutoff else all_targets
            )
            n = len(targets)
            periods = period_returns(targets, end_date=today, prices=prices)
            m = _metrics_from_periods(periods)
            turnover = _avg_turnover(targets)
            cost_total = LLMCall.objects.filter(portfolio_target__strategy=s)
            if cutoff:
                cost_total = cost_total.filter(created_at__date__gte=cutoff)
            cost_sum = float(cost_total.aggregate(s=Sum("cost_usd"))["s"] or 0)
            avg_cost = cost_sum / n if n else None
            ca = _council_alpha(targets, nav, cost_sum)
            ppy = m["ppy"] if m else None
            StrategyScorecard.objects.create(**_fit_decimals(StrategyScorecard, {
                "strategy": s, "user_id": s.user_id, "flavor": s.kind,
                "window": window, "as_of": today,
                "n_cycles": n,
                "n_observations": m["n"] if m else 0,
                "periods_per_year": ppy,
                "total_return_pct": m["total"] * 100 if m else None,
                "annualised_return_pct": m["ann"] * 100 if m and m["ann"] is not None else None,
                "sharpe": m["sharpe"] if m else None,
                "sortino": m["sortino"] if m else None,
                "sortino_note": (m["sortino_note"] if m else "no priced observations"),
                "max_drawdown_pct": m["dd"] * 100 if m else None,
                "hit_rate": m["hit"] if m else None,
                "annualised_turnover_pct": (
                    turnover * (ppy or ANNUALIZE_CYCLES) * 100 if turnover is not None else None
                ),
                "avg_cost_per_cycle_usd": avg_cost,
                "council_alpha_bps": ca["alpha_bps"],
                "council_cost_usd": ca["cost_usd"],
                "council_net_value_usd": ca["net_value_usd"],
                "baseline_version": BASELINE_VERSION,
                "provisional": (m or {}).get("provisional", True),
                "metrics_version": METRICS_VERSION,
            }))
            by_flavor.setdefault((s.user_id, s.kind, window), []).append(m)

    # Per-flavor aggregate: median across THIS USER's strategies of that flavor.
    # (Before wave 3 these rows had no owner and every authenticated caller was
    # served the same global median — see the feresearch scope proof test.)
    for (user_id, flavor, window), metric_list in by_flavor.items():
        # A flavor the user actually runs always gets a row, even when nothing in
        # it could be priced — an honest "no measurable observations" row beats a
        # silently missing benchmark.
        valid = [m for m in metric_list if m is not None]

        def _vals(key, _rows=valid):
            return [x[key] for x in _rows if x.get(key) is not None]

        def _med(key):
            vals = _vals(key)
            return statistics.median(vals) if vals else None

        def _iqr(key):
            """(p25, p75) across the flavor's strategies, or (None, None) if < 2."""
            vals = _vals(key)
            if len(vals) < 2:
                return None, None
            q = statistics.quantiles(vals, n=4)  # [p25, p50, p75]
            return q[0], q[2]

        dd25, dd75 = _iqr("dd")
        sh25, sh75 = _iqr("sharpe")
        so25, so75 = _iqr("sortino")
        med_total = _med("total")
        med_dd = _med("dd")
        # A flavor row is only "real" when at least one contributing strategy
        # cleared the provisional bar; otherwise it is a median of small samples.
        non_provisional = [m for m in valid if not m.get("provisional")]
        StrategyScorecard.objects.create(**_fit_decimals(StrategyScorecard, {
            "strategy": None, "user_id": user_id, "flavor": flavor,
            "window": window, "as_of": today,
            "n_cycles": len(metric_list),  # for flavor rows this is "n strategies"
            "n_observations": sum(m["n"] for m in valid),
            "periods_per_year": _med("ppy"),
            "total_return_pct": med_total * 100 if med_total is not None else None,
            "sharpe": _med("sharpe"),
            "sortino": _med("sortino"),
            "max_drawdown_pct": med_dd * 100 if med_dd is not None else None,
            "hit_rate": _med("hit"),
            "sharpe_p25": sh25, "sharpe_p75": sh75,
            "sortino_p25": so25, "sortino_p75": so75,
            "max_drawdown_p25_pct": dd25 * 100 if dd25 is not None else None,
            "max_drawdown_p75_pct": dd75 * 100 if dd75 is not None else None,
            "provisional": not non_provisional,
            "metrics_version": METRICS_VERSION,
        }))


def council_alpha_series(strategy, cutoff: dt.date | None = None) -> list[dict]:
    """Per-cycle realised vs council-free-baseline returns for one strategy, with
    running cumulative compounding — the drill-down data behind the council-alpha
    chart. Read-only: uses the persisted ``marked_snapshot`` (realised) and
    ``baseline_marked_snapshot`` (baseline, populated by the nightly recompute).
    Cycles without a captured baseline are skipped (council-alpha is forward-only).
    """
    tq = PortfolioTarget.objects.filter(
        strategy=strategy, status=PortfolioTarget.DONE
    ).order_by("as_of_date")
    if cutoff:
        tq = tq.filter(as_of_date__gte=cutoff)
    rows: list[dict] = []
    cum_r, cum_b = 1.0, 1.0
    for t in tq.iterator():
        if not (t.baseline_weights or {}):
            continue
        rv = (t.marked_snapshot or {}).get("since_as_of_pct")
        bv = (t.baseline_marked_snapshot or {}).get("since_as_of_pct")
        if rv in (None, "", "None") or bv in (None, "", "None"):
            continue
        try:
            r = float(rv) / 100.0
            b = float(bv) / 100.0
        except (TypeError, ValueError):
            continue
        cum_r *= 1 + r
        cum_b *= 1 + b
        rows.append({
            "as_of_date": t.as_of_date.isoformat(),
            "realised_pct": round(r * 100, 4),
            "baseline_pct": round(b * 100, 4),
            "cum_realised_pct": round((cum_r - 1) * 100, 4),
            "cum_baseline_pct": round((cum_b - 1) * 100, 4),
        })
    return rows


def recompute_all(today: dt.date | None = None, forward_days: int = DEFAULT_FORWARD_DAYS) -> dict:
    """Rebuild every scorecard for ``today`` under the current maths.

    Idempotent: each ``recompute_*`` deletes its own ``as_of=today`` rows before
    writing, so re-running is safe (and is how a prod operator upgrades rows
    written by an older ``metrics_version``).
    """
    today = today or timezone.localdate()
    recompute_agents(today, forward_days)
    recompute_models(today, forward_days)
    recompute_strategies(today)
    return {
        "as_of": today.isoformat(),
        "metrics_version": METRICS_VERSION,
        "agents": AgentScorecard.objects.filter(as_of=today).count(),
        "models": ModelScorecard.objects.filter(as_of=today).count(),
        "strategies": StrategyScorecard.objects.filter(as_of=today).count(),
    }


# Drill-down pagination: the agent decision list is bounded so one persona with
# a long history cannot pull an unbounded result set through the API.
DECISION_DETAIL_LIMIT = 200


def agent_decision_detail(
    agent_name: str, window: str = "90d", forward_days: int = DEFAULT_FORWARD_DAYS,
    today: dt.date | None = None, *, user=None, limit: int = DECISION_DETAIL_LIMIT,
    offset: int = 0,
) -> list[dict]:
    """Drill-down: the underlying per-decision rows behind an agent scorecard.

    ``user`` scopes the rows to that owner's runs — the endpoint used to serve
    every tenant's tickers, dates and model ids to any authenticated caller
    (F-xtenant). Forward returns are fetched in ONE query (F-n+1) and the page
    is bounded by ``limit``/``offset``.
    """
    today = today or timezone.localdate()
    cutoff = _cutoff(WINDOWS_AGENT.get(window), today)
    qs = AgentMessage.objects.filter(
        agent_name=agent_name, run__status=Run.DONE
    ).select_related("run")
    if cutoff:
        qs = qs.filter(run__as_of_date__gte=cutoff)
    if user is not None:
        qs = qs.filter(run__user=user)
    qs = qs.exclude(run__tickers=[]).order_by("-run__as_of_date", "-run__created_at")
    limit = max(1, min(int(limit or DECISION_DETAIL_LIMIT), DECISION_DETAIL_LIMIT))
    offset = max(0, int(offset or 0))
    messages = list(qs[offset:offset + limit])
    rows = [(m, m.run) for m in messages if m.run.tickers]
    returns = batch_forward_returns(
        [(str(run.tickers[0]).upper(), run.as_of_date) for _, run in rows], forward_days
    )
    out = []
    for m, run in rows:
        po = m.parsed_output or {}
        ticker = str(run.tickers[0]).upper()
        ret = returns.get((ticker, run.as_of_date))
        out.append({
            "run_id": run.id,
            "ticker": ticker,
            "as_of_date": run.as_of_date.isoformat(),
            "signal": po.get("signal"),
            "confidence": po.get("confidence"),
            "model_id": (run.model_overrides or {}).get(agent_name, ""),
            "forward_return_pct": round(ret * 100, 2) if ret is not None else None,
        })
    return out
