from django.urls import path

from .views import (
    AttributionView,
    BacktestCancelView,
    BacktestCompareView,
    BacktestDetailView,
    BacktestListCreateView,
    DefaultUniverseView,
    DeflationView,
    EquityCurveView,
    FoldsView,
)

urlpatterns = [
    path("backtests/", BacktestListCreateView.as_view(), name="backtest-list-create"),
    path("backtests/default-universe/", DefaultUniverseView.as_view(), name="backtest-default-universe"),
    path("backtests/compare/", BacktestCompareView.as_view(), name="backtest-compare"),
    path("backtests/<int:pk>/", BacktestDetailView.as_view(), name="backtest-detail"),
    path("backtests/<int:pk>/cancel/", BacktestCancelView.as_view(), name="backtest-cancel"),
    path("backtests/<int:pk>/equity-curve/", EquityCurveView.as_view(), name="backtest-equity"),
    path("backtests/<int:pk>/folds/", FoldsView.as_view(), name="backtest-folds"),
    path("backtests/<int:pk>/deflation/", DeflationView.as_view(), name="backtest-deflation"),
    path("backtests/<int:pk>/attribution/", AttributionView.as_view(), name="backtest-attribution"),
]
