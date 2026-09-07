"""Materialized leaderboard scorecards (P3b).

Recomputed nightly (pure Python, no LLM). Rows are keyed by ``as_of`` so the API
serves the latest snapshot per window and history is retained. ``provisional``
flags low-sample rows the UI greys out (plan risk #2 — statistical honesty).
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

# Generation of the leaderboard scoring maths. Bump whenever a change makes
# previously-written numbers non-comparable; the API refuses to present ratios
# from rows below this and the nightly/management recompute rewrites them.
#   1 (implicit, stored as 0) — overlapping cumulative windows chained as if
#       independent, hard-coded 252 annualisation, unbounded Sortino, re-runs
#       counted as separate decisions.
#   2 — disjoint per-period returns, annualised by the observed cadence,
#       bounded Sortino, deduped + non-overlapping agent decisions.
METRICS_VERSION = 2


class AgentScorecard(models.Model):
    """Signal accuracy + PnL per (agent, version, model) over a rolling window."""

    # Scorecards are per-owner: they score THIS user's runs. Legacy rows written
    # before the wave-3 fix are global (user is null) and are dropped by the
    # 0006 data migration — see ``metrics_version``.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="agent_scorecards",
    )
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
    # Disagreement value ("useful contrarians"): of this agent's directional
    # calls that went AGAINST the run's majority-persona consensus, how often
    # was it right? A high contrarian hit-rate flags personas worth weighting
    # more in the PM aggregation (plan §"disagreement value").
    n_contrarian_decisions = models.IntegerField(default=0)
    contrarian_hit_rate = models.DecimalField(
        max_digits=6, decimal_places=4, null=True, blank=True
    )
    contrarian_hit_rate_ci_low = models.DecimalField(
        max_digits=6, decimal_places=4, null=True, blank=True
    )
    contrarian_hit_rate_ci_high = models.DecimalField(
        max_digits=6, decimal_places=4, null=True, blank=True
    )
    provisional = models.BooleanField(default=True)
    # Which generation of the scoring maths produced this row. 0/1 = pre-wave-3
    # (overlapping windows, hard-coded 252 annualisation, duplicate re-runs
    # counted as independent decisions); 2 = the fixed maths. The API never
    # presents a ratio from a row below METRICS_VERSION.
    metrics_version = models.PositiveSmallIntegerField(default=0, db_index=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "user", "agent_name", "agent_version", "model_id", "window", "as_of",
                ],
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
    # Owner of the row. Per-strategy rows mirror ``strategy.user``; flavor
    # aggregates are per-user too, so "median across YOUR strategies of this
    # flavor" is actually true (before wave 3 the flavor rows had no user column
    # at all and every authenticated caller was served the same global median).
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="strategy_scorecards",
    )
    flavor = models.CharField(max_length=20)
    window = models.CharField(max_length=16)  # 30d|90d|ytd|lifetime
    as_of = models.DateField(db_index=True)

    # Cycles run in the window (what the strategy did).
    n_cycles = models.IntegerField(default=0)
    # DISJOINT holding intervals actually priced — the sample size behind every
    # ratio below. Lower than ``n_cycles`` when a cycle could not be marked.
    n_observations = models.IntegerField(default=0)
    # Annualisation factor derived from the OBSERVED cadence (median calendar
    # gap between cycles), not a hard-coded 252. Null when unknown.
    periods_per_year = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True
    )
    total_return_pct = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    annualised_return_pct = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True
    )
    sharpe = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    sortino = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    # Why ``sortino`` is null or capped ("" when it is a plain number). A
    # near-zero downside deviation used to divide its way to Sortino 911.
    sortino_note = models.CharField(max_length=64, blank=True, default="")
    max_drawdown_pct = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    hit_rate = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
    annualised_turnover_pct = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True
    )
    avg_cost_per_cycle_usd = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True
    )
    # Interquartile range (p25/p75) of each metric across the flavor's strategies —
    # set only on flavor-aggregate rows (strategy is null), so the benchmark view
    # shows median + dispersion, not just a point estimate (plan acceptance: "median
    # + IQR per flavor"). Null on per-strategy rows and when n < 2.
    sharpe_p25 = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    sharpe_p75 = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    sortino_p25 = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    sortino_p75 = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    max_drawdown_p25_pct = models.DecimalField(
        max_digits=8, decimal_places=4, null=True, blank=True
    )
    max_drawdown_p75_pct = models.DecimalField(
        max_digits=8, decimal_places=4, null=True, blank=True
    )
    # Council-alpha: annualised (realised − council-free baseline) in bps.
    # Null until ≥ MIN_COUNCIL_ALPHA_CYCLES paired cycles exist (UI shows "—"
    # + "needs 30 days of baseline" tooltip). ``council_cost_usd`` is the
    # cumulative council LLM spend over the window; ``council_net_value_usd`` =
    # (realised − baseline) × NAV − council cost = the dollars the council
    # produced (or destroyed). ``baseline_version`` stamps the baseline def.
    council_alpha_bps = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    council_cost_usd = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    council_net_value_usd = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    baseline_version = models.CharField(max_length=16, blank=True, default="")
    # True when the sample is too small for a ratio to mean anything
    # (``n_observations`` < 20). Such rows keep n_cycles / total_return_pct /
    # hit_rate / max_drawdown_pct but carry NULL sharpe / sortino /
    # annualised_return_pct, and the API sorts them below real rows.
    provisional = models.BooleanField(default=True)
    # See ``AgentScorecard.metrics_version``. 2 = disjoint per-period returns,
    # cadence-based annualisation, bounded Sortino.
    metrics_version = models.PositiveSmallIntegerField(default=0, db_index=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "strategy", "flavor", "window", "as_of"],
                name="uniq_strategy_scorecard",
            ),
        ]
        indexes = [models.Index(fields=["flavor", "window", "as_of"])]

    def __str__(self) -> str:
        who = f"strategy {self.strategy_id}" if self.strategy_id else f"flavor {self.flavor}"
        return f"{who} [{self.window}]"
