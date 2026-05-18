"""Per-agent attribution on the stitched OOS curve.

For each agent A: re-aggregate every fold's OOS with `signal[A]=neutral,
confidence[A]=0`, stitch the resulting equity, and report the PnL delta
vs the original.
"""
from __future__ import annotations

import copy

from .engine import rebalance_dates_for, run_segment, trading_days
from .models import BacktestMetrics


def _neutralize(cache: dict, agent: str) -> dict:
    """Deep-clone the cache and zero out persona/analytical signal for one agent."""
    cloned: dict = {}
    for key, entry in cache.items():
        e = copy.deepcopy(entry)
        if agent in e:
            if "signal" in e[agent]:
                e[agent]["signal"] = "neutral"
            if "confidence" in e[agent]:
                e[agent]["confidence"] = 0
        cloned[key] = e
    return cloned


def _final_equity(bt, fold_records, cache, pm_config_by_fold=None) -> float:
    """Replay every fold OOS, chaining cumulative returns. Returns final equity."""
    eq = float(bt.starting_cash)
    for f in fold_records:
        cfg = (pm_config_by_fold or {}).get(f.id) or f.winning_config
        days = trading_days(f.oos_start, f.oos_end, list(bt.universe))
        rebal = rebalance_dates_for(days, bt.rebalance_frequency)
        seg = run_segment(
            bt=bt, start=f.oos_start, end=f.oos_end,
            pm_config=cfg, agent_outputs_cache=cache, rebalance_dates=rebal,
        )
        if len(seg.equity) >= 2:
            ratio = seg.equity[-1] / seg.equity[0]
            eq = eq * ratio
    return eq


def compute_attribution(
    bt, fold_records, agent_outputs_cache, personas: list[str]
) -> None:
    """Persist `BacktestMetrics.per_agent_attribution = {agent: delta_pnl}`.

    delta_pnl = full_final_equity - without_agent_final_equity
    """
    full_eq = _final_equity(bt, fold_records, agent_outputs_cache)
    attribution = {}
    for agent in personas:
        neutered = _neutralize(agent_outputs_cache, agent)
        without_eq = _final_equity(bt, fold_records, neutered)
        attribution[agent] = round(full_eq - without_eq, 2)

    metrics, _ = BacktestMetrics.objects.get_or_create(backtest=bt)
    metrics.per_agent_attribution = attribution
    metrics.save(update_fields=["per_agent_attribution"])
