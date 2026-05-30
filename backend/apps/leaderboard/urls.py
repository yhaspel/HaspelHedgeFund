from django.urls import path

from .views import (
    AgentDecisionsView,
    AgentLeaderboardView,
    FlavorBenchmarkView,
    ModelLeaderboardView,
    RecomputeView,
    StrategyLeaderboardView,
)

urlpatterns = [
    path("leaderboard/agents/", AgentLeaderboardView.as_view(), name="leaderboard-agents"),
    path(
        "leaderboard/agents/<str:agent_name>/decisions/",
        AgentDecisionsView.as_view(),
        name="leaderboard-agent-decisions",
    ),
    path("leaderboard/models/", ModelLeaderboardView.as_view(), name="leaderboard-models"),
    path(
        "leaderboard/strategies/",
        StrategyLeaderboardView.as_view(),
        name="leaderboard-strategies",
    ),
    path(
        "leaderboard/strategies/by-flavor/",
        FlavorBenchmarkView.as_view(),
        name="leaderboard-flavor",
    ),
    path("leaderboard/recompute/", RecomputeView.as_view(), name="leaderboard-recompute"),
]
