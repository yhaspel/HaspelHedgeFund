"""Nightly leaderboard recompute (P3b) — pure Python, no LLM calls.

Agents: per-persona signal accuracy (hit-rate + Brier vs the forward N-day
return) plus a PnL column folded in from backtest per-agent attribution.
Strategies: rolling Sharpe/Sortino/drawdown/hit-rate/turnover/$-per-cycle from
``PortfolioTarget.marked_snapshot``, plus per-flavor median aggregates.

Statistical honesty: Wilson CIs on hit-rate, and a ``provisional`` flag for
low-sample rows (agents < 30 decisions, strategies < 20 cycles).
"""
from __future__ import annotations

import datetime as dt
import statistics
from decimal import Decimal

from django.db.models import Avg, Count, Sum
from django.utils import timezone

from apps.backtests.metrics import drawdown_pct, sharpe_ratio, sortino_ratio
from apps.backtests.metrics import hit_rate as hit_rate_fn
from apps.backtests.models import BacktestMetrics
from apps.models_catalog.presets import ANALYTICAL_AGENTS, PERSONA_AGENTS
from apps.portfolios.cycle_mark import ensure_baseline_snapshot
from apps.portfolios.models import PortfolioStrategy, PortfolioTarget
from apps.runs.models import AgentMessage, Run
from hedgefund_agents.models import LLMCall

from .council_alpha import BASELINE_VERSION
from .forward_returns import DEFAULT_FORWARD_DAYS, brier, forward_return, wilson_interval
from .models import AgentScorecard, ModelScorecard, StrategyScorecard

WINDOWS_AGENT = {"30d": 30, "90d": 90, "lifetime": None}
WINDOWS_STRATEGY = {"30d": 30, "90d": 90, "ytd": "ytd", "lifetime": None}
MIN_DECISIONS = 30
MIN_CYCLES = 20
# Council-alpha needs more paired observations than the generic provisional
# threshold before it's worth quoting (plan acceptance criterion: ≥30 days of
# baseline). Below this, council_alpha_bps stays null and the UI shows "needs
# 30 days of baseline".
MIN_COUNCIL_ALPHA_CYCLES = 30
# Per-cycle ratios annualize assuming ~daily cycles. Surfaced as a UI caveat.
ANNUALIZE_CYCLES = 252


# ---- small helpers ----

def _dec4(v):
    return Decimal(str(round(v, 4))) if v is not None else None


def _dec2(v):
    return Decimal(str(round(v, 2))) if v is not None else None


def _dec6(v):
    return Decimal(str(round(v, 6))) if v is not None else None


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


# ---- agents ----

def _agent_pnl_bps(agent: str) -> float | None:
    """Mean per-agent PnL contribution (bps of starting NAV) across backtests
    whose attribution covers this agent. None when no backtest covers it."""
    vals = []
    qs = BacktestMetrics.objects.exclude(per_agent_attribution={}).select_related("backtest")
    for m in qs.iterator():
        attr = m.per_agent_attribution or {}
        if agent not in attr:
            continue
        try:
            dollars = float(attr[agent])
            cash = float(m.backtest.starting_cash or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if cash > 0:
            vals.append(dollars / cash * 10000.0)
    return statistics.fmean(vals) if vals else None


def _collect_agent_decisions(cutoff, forward_days: int) -> list[dict]:
    qs = AgentMessage.objects.filter(
        agent_name__in=PERSONA_AGENTS, run__status=Run.DONE
    ).select_related("run")
    if cutoff:
        qs = qs.filter(run__created_at__date__gte=cutoff)
    out = []
    for m in qs.iterator():
        run = m.run
        if not run.tickers:
            continue
        po = m.parsed_output or {}
        sig = po.get("signal")
        if sig not in ("bullish", "neutral", "bearish"):
            continue
        ret = forward_return(run.tickers[0], run.as_of_date, forward_days)
        if ret is None:
            continue
        directional = sig in ("bullish", "bearish")
        hit = None
        signed = 0.0
        if directional:
            hit = (sig == "bullish" and ret > 0) or (sig == "bearish" and ret < 0)
            signed = ret if sig == "bullish" else -ret
        out.append({
            "agent": m.agent_name,
            "version": (run.agent_versions or {}).get(m.agent_name, ""),
            "model": (run.model_overrides or {}).get(m.agent_name, ""),
            "conf": int(po.get("confidence", 0) or 0),
            "ret": signed,
            "hit": hit,
            "directional": directional,
        })
    return out


def recompute_agents(today: dt.date, forward_days: int = DEFAULT_FORWARD_DAYS) -> None:
    AgentScorecard.objects.filter(as_of=today).delete()
    pnl_cache: dict[str, float | None] = {}
    for window, days in WINDOWS_AGENT.items():
        decisions = _collect_agent_decisions(_cutoff(days, today), forward_days)
        groups: dict[tuple, list] = {}
        for d in decisions:
            groups.setdefault((d["agent"], d["version"], d["model"]), []).append(d)
        for (agent, version, model), rows in groups.items():
            if agent not in pnl_cache:
                pnl_cache[agent] = _agent_pnl_bps(agent)
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
            AgentScorecard.objects.create(
                agent_name=agent, agent_version=version, model_id=model,
                window=window, as_of=today, n_decisions=len(rows), n_directional=nd,
                hit_rate=_dec4(hit_rate), hit_rate_ci_low=_dec4(ci_low),
                hit_rate_ci_high=_dec4(ci_high), brier_score=_dec4(brier_score),
                avg_forward_return_bps=_dec2(avg_ret_bps),
                pnl_contribution_bps=_dec2(pnl_cache[agent]),
                provisional=nd < MIN_DECISIONS,
            )


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
            ModelScorecard.objects.create(
                model_id=model, agent_role=role, window=window, as_of=today,
                n_decisions=n, avg_cost_per_decision_usd=_dec6(avg_cost),
                avg_forward_return_bps=_dec2(avg_ret_bps),
                cost_adjusted_return_bps=_dec2(cost_adj),
                provisional=n < MIN_DECISIONS,
            )


# ---- strategies ----

def _cycle_returns(targets) -> list[float]:
    rets = []
    for t in targets:
        v = (t.marked_snapshot or {}).get("since_as_of_pct")
        if v in (None, "", "None"):
            continue
        try:
            rets.append(float(v) / 100.0)
        except (TypeError, ValueError):
            continue
    return rets


def _avg_turnover(targets) -> float | None:
    """Mean per-cycle one-way turnover (fraction of NAV) from target_weight
    deltas between consecutive cycles. Weights are signed percent."""
    weights = [t.target_weights or {} for t in targets]
    if len(weights) < 2:
        return None
    turns = []
    for prev, cur in zip(weights, weights[1:], strict=False):
        keys = set(prev) | set(cur)
        delta = sum(abs(float(cur.get(k, 0)) - float(prev.get(k, 0))) for k in keys)
        turns.append(delta / 2.0 / 100.0)
    return statistics.fmean(turns) if turns else None


def _metrics_from_returns(rets: list[float]) -> dict | None:
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


def _paired_returns(targets) -> tuple[list[float], list[float]]:
    """Realised vs council-free-baseline per-cycle returns, over the cycles
    that captured a baseline (council-alpha is forward-only). Marks each
    baseline book with the same forward-return machinery, then pairs by cycle.
    """
    realised: list[float] = []
    baseline: list[float] = []
    for t in targets:
        if not (t.baseline_weights or {}):
            continue
        rv = (t.marked_snapshot or {}).get("since_as_of_pct")
        bv = (ensure_baseline_snapshot(t) or {}).get("since_as_of_pct")
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
    for s in PortfolioStrategy.objects.select_related("portfolio").iterator():
        for window, days in WINDOWS_STRATEGY.items():
            cutoff = _cutoff(days, today)
            tq = PortfolioTarget.objects.filter(
                strategy=s, status=PortfolioTarget.DONE
            ).order_by("as_of_date")
            if cutoff:
                tq = tq.filter(as_of_date__gte=cutoff)
            targets = list(tq)
            n = len(targets)
            m = _metrics_from_returns(_cycle_returns(targets))
            turnover = _avg_turnover(targets)
            cost_total = LLMCall.objects.filter(portfolio_target__strategy=s)
            if cutoff:
                cost_total = cost_total.filter(created_at__date__gte=cutoff)
            cost_sum = float(cost_total.aggregate(s=Sum("cost_usd"))["s"] or 0)
            avg_cost = cost_sum / n if n else None
            nav = float(getattr(s.portfolio, "cash_balance", 0) or 0)
            ca = _council_alpha(targets, nav, cost_sum)
            StrategyScorecard.objects.create(
                strategy=s, flavor=s.kind, window=window, as_of=today, n_cycles=n,
                total_return_pct=_dec2(m["total"] * 100) if m else None,
                annualised_return_pct=_dec2(m["ann"] * 100) if m else None,
                sharpe=_dec4(m["sharpe"]) if m else None,
                sortino=_dec4(m["sortino"]) if m else None,
                max_drawdown_pct=_dec2(m["dd"] * 100) if m else None,
                hit_rate=_dec4(m["hit"]) if m else None,
                annualised_turnover_pct=(
                    _dec2(turnover * ANNUALIZE_CYCLES * 100) if turnover is not None else None
                ),
                avg_cost_per_cycle_usd=_dec2(avg_cost),
                council_alpha_bps=_dec2(ca["alpha_bps"]),
                council_cost_usd=_dec2(ca["cost_usd"]),
                council_net_value_usd=_dec2(ca["net_value_usd"]),
                baseline_version=BASELINE_VERSION,
                provisional=n < MIN_CYCLES,
            )
            by_flavor.setdefault((s.kind, window), []).append(m)

    # Per-flavor aggregate: median across the user's strategies of that flavor.
    for (flavor, window), metric_list in by_flavor.items():
        valid = [m for m in metric_list if m is not None]
        if not valid:
            continue

        def _med(key, _rows=valid):
            vals = [x[key] for x in _rows if x.get(key) is not None]
            return statistics.median(vals) if vals else None

        StrategyScorecard.objects.create(
            strategy=None, flavor=flavor, window=window, as_of=today,
            n_cycles=len(valid),  # for flavor rows this is "n strategies"
            total_return_pct=_dec2(_med("total") * 100) if _med("total") is not None else None,
            sharpe=_dec4(_med("sharpe")),
            sortino=_dec4(_med("sortino")),
            max_drawdown_pct=_dec2(_med("dd") * 100) if _med("dd") is not None else None,
            hit_rate=_dec4(_med("hit")),
            provisional=False,
        )


def recompute_all(today: dt.date | None = None, forward_days: int = DEFAULT_FORWARD_DAYS) -> dict:
    today = today or timezone.localdate()
    recompute_agents(today, forward_days)
    recompute_models(today, forward_days)
    recompute_strategies(today)
    return {
        "as_of": today.isoformat(),
        "agents": AgentScorecard.objects.filter(as_of=today).count(),
        "models": ModelScorecard.objects.filter(as_of=today).count(),
        "strategies": StrategyScorecard.objects.filter(as_of=today).count(),
    }


def agent_decision_detail(
    agent_name: str, window: str = "90d", forward_days: int = DEFAULT_FORWARD_DAYS,
    today: dt.date | None = None,
) -> list[dict]:
    """Drill-down: the underlying per-decision rows behind an agent scorecard."""
    today = today or timezone.localdate()
    cutoff = _cutoff(WINDOWS_AGENT.get(window), today)
    qs = AgentMessage.objects.filter(
        agent_name=agent_name, run__status=Run.DONE
    ).select_related("run")
    if cutoff:
        qs = qs.filter(run__created_at__date__gte=cutoff)
    out = []
    for m in qs.order_by("-run__created_at").iterator():
        run = m.run
        if not run.tickers:
            continue
        po = m.parsed_output or {}
        ret = forward_return(run.tickers[0], run.as_of_date, forward_days)
        out.append({
            "run_id": run.id,
            "ticker": run.tickers[0],
            "as_of_date": run.as_of_date.isoformat(),
            "signal": po.get("signal"),
            "confidence": po.get("confidence"),
            "model_id": (run.model_overrides or {}).get(agent_name, ""),
            "forward_return_pct": round(ret * 100, 2) if ret is not None else None,
        })
    return out
