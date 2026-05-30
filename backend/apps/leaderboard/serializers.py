from __future__ import annotations

from rest_framework import serializers

from .models import AgentScorecard, ModelScorecard, StrategyScorecard


class AgentScorecardSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentScorecard
        fields = (
            "id", "agent_name", "agent_version", "model_id", "window", "as_of",
            "n_decisions", "n_directional", "hit_rate", "hit_rate_ci_low",
            "hit_rate_ci_high", "brier_score", "avg_forward_return_bps",
            "pnl_contribution_bps", "n_contrarian_decisions", "contrarian_hit_rate",
            "contrarian_hit_rate_ci_low", "contrarian_hit_rate_ci_high",
            "provisional", "last_updated",
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
            "window", "as_of", "n_cycles", "total_return_pct",
            "annualised_return_pct", "sharpe", "sortino", "max_drawdown_pct",
            "hit_rate", "annualised_turnover_pct", "avg_cost_per_cycle_usd",
            "council_alpha_bps", "council_cost_usd", "council_net_value_usd",
            "baseline_version", "provisional", "last_updated",
        )

    def get_flavor_display(self, obj: StrategyScorecard) -> str:
        from apps.portfolios.models import PortfolioStrategy

        return dict(PortfolioStrategy.KIND_CHOICES).get(obj.flavor, obj.flavor)
