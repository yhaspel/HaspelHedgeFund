"""Materialized leaderboard scorecards (P3b).

Recomputed nightly (pure Python, no LLM). Rows are keyed by ``as_of`` so the API
serves the latest snapshot per window and history is retained. ``provisional``
flags low-sample rows the UI greys out (plan risk #2 — statistical honesty).
"""
from __future__ import annotations

from django.db import models


class AgentScorecard(models.Model):
    """Signal accuracy + PnL per (agent, version, model) over a rolling window."""

    agent_name = models.CharField(max_length=64, db_index=True)
    agent_version = models.CharField(max_length=64, blank=True, default="")
    model_id = models.CharField(max_length=128, blank=True, default="")
    window = models.CharField(max_length=16)  # "30d" | "90d" | "lifetime"
    as_of = models.DateField(db_index=True)

    n_decisions = models.IntegerField(default=0)
    n_directional = models.IntegerField(default=0)
    # % of directional (bullish/bearish) calls whose forward N-day return agreed.
    hit_rate = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
    hit_rate_ci_low = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
    hit_rate_ci_high = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
    # Brier calibration of persona.confidence vs realized outcome (lower=better).
    brier_score = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
    # Mean signed forward return (bullish=+ret, bearish=−ret), in basis points.
    avg_forward_return_bps = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    # From backtest per-agent attribution (null until a backtest covers this agent).
    pnl_contribution_bps = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    provisional = models.BooleanField(default=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["agent_name", "agent_version", "model_id", "window", "as_of"],
                name="uniq_agent_scorecard",
            ),
        ]
        indexes = [models.Index(fields=["window", "as_of"])]

    def __str__(self) -> str:
        return f"{self.agent_name}/{self.model_id} [{self.window}]"


class ModelScorecard(models.Model):
    """Per (model, agent-role) cost + cost-adjusted accuracy over a window."""

    model_id = models.CharField(max_length=128, db_index=True)
    agent_role = models.CharField(max_length=32)  # persona|analytical|pm|risk|cio|macro|news
    window = models.CharField(max_length=16)
    as_of = models.DateField(db_index=True)

    n_decisions = models.IntegerField(default=0)
    avg_cost_per_decision_usd = models.DecimalField(
        max_digits=10, decimal_places=6, null=True, blank=True
    )
    avg_forward_return_bps = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    # avg_forward_return_bps per $ of cost (higher = better value).
    cost_adjusted_return_bps = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    provisional = models.BooleanField(default=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["model_id", "agent_role", "window", "as_of"],
                name="uniq_model_scorecard",
            ),
        ]
        indexes = [models.Index(fields=["window", "as_of"])]

    def __str__(self) -> str:
        return f"{self.model_id}/{self.agent_role} [{self.window}]"


class StrategyScorecard(models.Model):
    """Per-strategy (and per-flavor aggregate) rolling-window performance."""

    # null strategy => this row is the flavor aggregate ("your N strategies").
    strategy = models.ForeignKey(
        "portfolios.PortfolioStrategy",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="scorecards",
    )
    flavor = models.CharField(max_length=20)
    window = models.CharField(max_length=16)  # 30d|90d|ytd|lifetime
    as_of = models.DateField(db_index=True)

    n_cycles = models.IntegerField(default=0)
    total_return_pct = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    annualised_return_pct = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True
    )
    sharpe = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    sortino = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    max_drawdown_pct = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    hit_rate = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
    annualised_turnover_pct = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True
    )
    avg_cost_per_cycle_usd = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True
    )
    # Deferred to a P3b follow-up — always null in v1; UI shows "—" + tooltip.
    council_alpha_bps = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    provisional = models.BooleanField(default=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["strategy", "flavor", "window", "as_of"],
                name="uniq_strategy_scorecard",
            ),
        ]
        indexes = [models.Index(fields=["flavor", "window", "as_of"])]

    def __str__(self) -> str:
        who = f"strategy {self.strategy_id}" if self.strategy_id else f"flavor {self.flavor}"
        return f"{who} [{self.window}]"
