from django.urls import path

from .api_autopilot import (
    StrategyAutopilotDisableView,
    StrategyAutopilotEnableView,
    StrategyAutopilotHistoryView,
    StrategyAutopilotResumeView,
    StrategyAutopilotRunNowView,
    StrategyAutopilotView,
    StrategyExecutedView,
)
from .api_fund import FundHaltView, FundOverviewView, FundResumeView
from .manual_book_views import (
    PortfolioCashView,
    PortfolioLedgerView,
    PortfolioOverviewView,
    PortfolioPositionCloseView,
    PortfolioPositionDetailView,
    PortfolioPositionsView,
    PortfolioPreferencesView,
    PortfolioRefreshMarksView,
    PortfolioSuggestionView,
)
from .views import (
    BorrowLookupView,
    CycleApproveCouncilView,
    CycleRejectView,
    PortfolioHubView,
    PortfolioListCreateView,
    PositionsView,
    StrategyCycleDetailView,
    StrategyCycleRefreshMarkView,
    StrategyCycleRerunView,
    StrategyCyclesView,
    StrategyDetailView,
    StrategyEnrollView,
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
    path("portfolios/hub/", PortfolioHubView.as_view(), name="portfolios-hub"),
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
    path(
        "strategies/<int:pk>/cycles/<int:target_id>/refresh-mark/",
        StrategyCycleRefreshMarkView.as_view(),
        name="strategy-cycle-refresh-mark",
    ),
    # P4 WS-B: rerun a terminal (failed/cancelled) cycle.
    path(
        "strategies/<int:pk>/cycles/<int:target_id>/rerun/",
        StrategyCycleRerunView.as_view(),
        name="strategy-cycle-rerun",
    ),
    # P4 WS-E: preview (GET) / apply (POST) cycle enrollment into the book.
    path(
        "strategies/<int:pk>/enroll/<int:target_id>/",
        StrategyEnrollView.as_view(),
        name="strategy-enroll",
    ),

    path("borrow/<str:ticker>/", BorrowLookupView.as_view(), name="borrow-lookup"),

    # P3: Manual Book — singular `/api/portfolio/...` so the existing
    # plural `/api/portfolios/` strategy-book endpoints stay intact.
    path("portfolio/", PortfolioOverviewView.as_view(), name="portfolio-overview"),
    path("portfolio/positions/", PortfolioPositionsView.as_view(),
         name="portfolio-positions"),
    path("portfolio/positions/<int:position_id>/",
         PortfolioPositionDetailView.as_view(), name="portfolio-position-detail"),
    path("portfolio/positions/<int:position_id>/close/",
         PortfolioPositionCloseView.as_view(), name="portfolio-position-close"),
    path("portfolio/ledger/", PortfolioLedgerView.as_view(),
         name="portfolio-ledger"),
    path("portfolio/cash/", PortfolioCashView.as_view(), name="portfolio-cash"),
    path("portfolio/position-suggestion/",
         PortfolioSuggestionView.as_view(), name="portfolio-position-suggestion"),
    path("portfolio/preferences/", PortfolioPreferencesView.as_view(),
         name="portfolio-preferences"),
    path("portfolio/refresh-marks/", PortfolioRefreshMarksView.as_view(),
         name="portfolio-refresh-marks"),

    # P7 — per-strategy autopilot + fund rollup.
    path("strategies/<int:pk>/autopilot/", StrategyAutopilotView.as_view(),
         name="strategy-autopilot"),
    path("strategies/<int:pk>/autopilot/enable/", StrategyAutopilotEnableView.as_view(),
         name="strategy-autopilot-enable"),
    path("strategies/<int:pk>/autopilot/disable/", StrategyAutopilotDisableView.as_view(),
         name="strategy-autopilot-disable"),
    path("strategies/<int:pk>/autopilot/resume/", StrategyAutopilotResumeView.as_view(),
         name="strategy-autopilot-resume"),
    path("strategies/<int:pk>/autopilot/run-now/", StrategyAutopilotRunNowView.as_view(),
         name="strategy-autopilot-run-now"),
    path("strategies/<int:pk>/autopilot/history/", StrategyAutopilotHistoryView.as_view(),
         name="strategy-autopilot-history"),
    path("strategies/<int:pk>/executed/", StrategyExecutedView.as_view(),
         name="strategy-executed"),
    path("fund/", FundOverviewView.as_view(), name="fund-overview"),
    path("fund/halt/", FundHaltView.as_view(), name="fund-halt"),
    path("fund/resume/", FundResumeView.as_view(), name="fund-resume"),
]
