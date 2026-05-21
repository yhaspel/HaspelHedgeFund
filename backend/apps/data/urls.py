from django.urls import path

from .views import (
    MacroSnapshotView,
    RegimeBatchView,
    RegimeHistoryView,
    RegimeSnapshotView,
    TickerNewsView,
    TickerSparklineView,
)

urlpatterns = [
    path("macro/snapshot/", MacroSnapshotView.as_view(), name="macro-snapshot"),
    path("macro/regime/batch/", RegimeBatchView.as_view(), name="macro-regime-batch"),
    path("macro/regime/<str:ticker>/", RegimeSnapshotView.as_view(), name="macro-regime"),
    path(
        "macro/regime/<str:ticker>/history/",
        RegimeHistoryView.as_view(),
        name="macro-regime-history",
    ),
    path("tickers/<str:ticker>/news/", TickerNewsView.as_view(), name="ticker-news"),
    path("tickers/<str:ticker>/sparkline/", TickerSparklineView.as_view(), name="ticker-sparkline"),
]
