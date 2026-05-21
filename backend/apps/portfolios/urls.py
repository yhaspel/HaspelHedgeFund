from django.urls import path

from .views import (
    BorrowLookupView,
    CycleApproveCouncilView,
    CycleRejectView,
    PortfolioListCreateView,
    PositionsView,
    StrategyCycleDetailView,
    StrategyCyclesView,
    StrategyDetailView,
    StrategyEstimateView,
    StrategyListCreateView,
    StrategyRunNowView,
    UniverseListView,
    UniverseMembershipView,
)

urlpatterns = [
    path("universes/", UniverseListView.as_view(), name="universes"),
    path("universes/<str:name>/members/", UniverseMembershipView.as_view(),
         name="universe-members"),

    path("portfolios/", PortfolioListCreateView.as_view(), name="portfolios"),
    path("portfolios/<int:portfolio_id>/positions/", PositionsView.as_view(),
         name="positions"),

    path("strategies/", StrategyListCreateView.as_view(), name="strategies"),
    path("strategies/<int:pk>/", StrategyDetailView.as_view(), name="strategy-detail"),
    path("strategies/<int:pk>/estimate/", StrategyEstimateView.as_view(), name="strategy-estimate"),
    path("strategies/<int:pk>/run-now/", StrategyRunNowView.as_view(), name="strategy-run-now"),
    path("strategies/<int:pk>/cycles/", StrategyCyclesView.as_view(),
         name="strategy-cycles"),
    path("strategies/<int:pk>/cycles/<int:target_id>/", StrategyCycleDetailView.as_view(),
         name="strategy-cycle-detail"),
    # P2l: manual-gate cycle approve / reject.
    path(
        "strategies/<int:pk>/cycles/<int:target_id>/approve-council/",
        CycleApproveCouncilView.as_view(),
        name="strategy-cycle-approve-council",
    ),
    path(
        "strategies/<int:pk>/cycles/<int:target_id>/reject/",
        CycleRejectView.as_view(),
        name="strategy-cycle-reject",
    ),

    path("borrow/<str:ticker>/", BorrowLookupView.as_view(), name="borrow-lookup"),
]
