from django.contrib import admin

from .models import AgentScorecard, ModelScorecard, StrategyScorecard


@admin.register(AgentScorecard)
class AgentScorecardAdmin(admin.ModelAdmin):
    list_display = (
        "agent_name", "model_id", "user", "window", "as_of", "hit_rate",
        "n_directional", "provisional", "metrics_version",
    )
    list_filter = ("window", "provisional", "metrics_version")


@admin.register(ModelScorecard)
class ModelScorecardAdmin(admin.ModelAdmin):
    list_display = (
        "model_id", "agent_role", "window", "as_of", "n_decisions",
        "avg_cost_per_decision_usd",
    )
    list_filter = ("window", "agent_role")


@admin.register(StrategyScorecard)
class StrategyScorecardAdmin(admin.ModelAdmin):
    list_display = (
        "__str__", "user", "flavor", "window", "as_of", "sharpe", "n_cycles",
        "n_observations", "provisional", "metrics_version",
    )
    list_filter = ("window", "flavor", "provisional", "metrics_version")
