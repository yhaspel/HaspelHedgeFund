from __future__ import annotations

from rest_framework import serializers

from .models import METRICS_VERSION, AgentScorecard, ModelScorecard, StrategyScorecard

# Ratios written by an older generation of the maths are never presented as
# real ratios. The 0006 data migration already NULLs them in the database; this
# is the API-side belt-and-braces so a row that somehow survives (a restored
# backup, a partially-applied deploy) still cannot be read as a Sharpe.
STALE_NOTE = f"stale: awaiting recompute under metrics_version {METRICS_VERSION}"
_STALE_RATIO_FIELDS = ("sharpe", "sortino", "annualised_return_pct",
                       "sharpe_p25", "sharpe_p75", "sortino_p25", "sortino_p75")


class AgentScorecardSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentScorecard
        fields = (
            "id", "agent_name", "agent_version", "model_id", "window", "as_of",
            "n_decisions", "n_directional", "hit_rate", "hit_rate_ci_low",
            "hit_rate_ci_high", "brier_score", "avg_forward_return_bps",
            "pnl_contribution_bps", "n_contrarian_decisions", "contrarian_hit_rate",
            "contrarian_hit_rate_ci_low", "contrarian_hit_rate_ci_high",
            "provisional", "metrics_version", "last_updated",
        )


class ModelScorecardSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModelScorecard
        fields = (
            "id", "model_id", "agent_role", "window", "as_of", "n_decisions",
            "avg_cost_per_decision_usd", "avg_forward_return_bps",
            "cost_adjusted_return_bps", "provisional", "last_updated",
        )


class StrategyScorecardSerializer(serializers.ModelSerializer):
    strategy_name = serializers.CharField(source="strategy.name", default=None, read_only=True)
    flavor_display = serializers.SerializerMethodField()

    class Meta:
        model = StrategyScorecard
        fields = (
            "id", "strategy", "strategy_name", "flavor", "flavor_display",
            "window", "as_of", "n_cycles", "n_observations", "periods_per_year",
            "total_return_pct",
            "annualised_return_pct", "sharpe", "sortino", "sortino_note",
            "max_drawdown_pct",
            "hit_rate", "annualised_turnover_pct", "avg_cost_per_cycle_usd",
            "sharpe_p25", "sharpe_p75", "sortino_p25", "sortino_p75",
            "max_drawdown_p25_pct", "max_drawdown_p75_pct",
            "council_alpha_bps", "council_cost_usd", "council_net_value_usd",
            "baseline_version", "provisional", "metrics_version", "last_updated",
        )

    def get_flavor_display(self, obj: StrategyScorecard) -> str:
        from apps.portfolios.models import PortfolioStrategy

        return dict(PortfolioStrategy.KIND_CHOICES).get(obj.flavor, obj.flavor)

    def to_representation(self, instance: StrategyScorecard) -> dict:
        data = super().to_representation(instance)
        if (instance.metrics_version or 0) < METRICS_VERSION:
            for field in _STALE_RATIO_FIELDS:
                data[field] = None
            data["sortino_note"] = STALE_NOTE
            data["provisional"] = True
        return data
