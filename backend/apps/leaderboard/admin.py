from django.contrib import admin

from .models import AgentScorecard, ModelScorecard, StrategyScorecard


@admin.register(AgentScorecard)
class AgentScorecardAdmin(admin.ModelAdmin):
    list_display = (
        "agent_name", "model_id", "window", "as_of", "hit_rate",
        "n_directional", "provisional",
    )
    list_filter = ("window", "provisional")


@admin.register(ModelScorecard)
class ModelScorecardAdmin(admin.ModelAdmin):
    list_display = (
        "model_id", "agent_role", "window", "as_of", "n_decisions",
        "avg_cost_per_decision_usd",
    )
    list_filter = ("window", "agent_role")


@admin.register(StrategyScorecard)
class StrategyScorecardAdmin(admin.ModelAdmin):
    list_display = ("__str__", "flavor", "window", "as_of", "sharpe", "n_cycles", "provisional")
    list_filter = ("window", "flavor", "provisional")
