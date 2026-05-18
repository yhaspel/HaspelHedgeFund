"""IS hyperparameter optimizer (random search).

Search dimensions (configurable per Backtest.search_space):
  - PM weights per persona (Dirichlet sample, sum=1)
  - buy_threshold in [0.10, 0.40]
  - sell_threshold in [-0.40, -0.10]
  - min_confidence in [0.5, 0.9]
  - vol_target_annual in [0.08, 0.20]
  - max_weight in [0.03, 0.15]

The optimizer scores each candidate by running `run_segment` over the IS
range against cached agent outputs (no LLM calls). Default objective is
Sharpe; Sortino and Calmar are also supported.
"""
from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass

from .engine import rebalance_dates_for, run_segment, trading_days
from .metrics import calmar_ratio, sharpe_ratio, sortino_ratio

DEFAULT_SEARCH_SPACE = {
    "buy_threshold": [0.10, 0.40],
    "sell_threshold": [-0.40, -0.10],
    "min_confidence": [0.5, 0.9],
    "vol_target_annual": [0.08, 0.20],
    "max_weight": [0.03, 0.15],
}


@dataclass
class Candidate:
    config: dict
    score: float = 0.0
    return_pct: float = 0.0


def sample_candidate(
    *, rng: random.Random, personas: list[str], search_space: dict
) -> dict:
    space = {**DEFAULT_SEARCH_SPACE, **(search_space or {})}

    def u(name: str) -> float:
        lo, hi = space[name]
        return rng.uniform(lo, hi)

    # Dirichlet weights (alpha=1.0 = uniform on simplex).
    raw = [rng.gammavariate(1.0, 1.0) for _ in personas]
    s = sum(raw) or 1.0
    weights = {p: w / s for p, w in zip(personas, raw)}

    return {
        "weights": weights,
        "buy_threshold": u("buy_threshold"),
        "sell_threshold": u("sell_threshold"),
        "min_confidence": u("min_confidence"),
        "vol_target_annual": u("vol_target_annual"),
        "max_weight": u("max_weight"),
    }


def score_config(
    *,
    bt,
    is_start: dt.date,
    is_end: dt.date,
    pm_config: dict,
    agent_outputs_cache: dict,
    objective: str = "sharpe",
) -> tuple[float, float]:
    """Return (objective_score, total_return_pct) for `pm_config` over the IS window."""
    days = trading_days(is_start, is_end, list(bt.universe))
    rebal = rebalance_dates_for(days, bt.rebalance_frequency)
    seg = run_segment(
        bt=bt, start=is_start, end=is_end, pm_config=pm_config,
        agent_outputs_cache=agent_outputs_cache, rebalance_dates=rebal,
    )
    equity = seg.equity
    if len(equity) < 2:
        return -1e9, 0.0
    rets = [(equity[i] / equity[i - 1]) - 1.0 for i in range(1, len(equity))]
    total_ret = equity[-1] / equity[0] - 1.0 if equity[0] > 0 else 0.0
    if objective == "sortino":
        score = sortino_ratio(rets)
    elif objective == "calmar":
        score = calmar_ratio(equity)
    else:
        score = sharpe_ratio(rets)
    return float(score), float(total_ret * 100)


def optimize_is(
    *,
    bt,
    is_start: dt.date,
    is_end: dt.date,
    agent_outputs_cache: dict,
    personas: list[str],
    n_candidates: int = 50,
    rng: random.Random | None = None,
) -> list[Candidate]:
    rng = rng or random.Random(int(getattr(bt, "rng_seed", 42)))
    space = bt.search_space or {}
    objective = bt.is_objective or "sharpe"
    results: list[Candidate] = []
    for _ in range(n_candidates):
        cfg = sample_candidate(rng=rng, personas=personas, search_space=space)
        score, ret_pct = score_config(
            bt=bt, is_start=is_start, is_end=is_end,
            pm_config=cfg, agent_outputs_cache=agent_outputs_cache,
            objective=objective,
        )
        results.append(Candidate(config=cfg, score=score, return_pct=ret_pct))
    results.sort(key=lambda c: c.score, reverse=True)
    return results
