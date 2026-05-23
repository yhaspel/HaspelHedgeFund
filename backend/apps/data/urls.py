from django.urls import path

from .views import (
    MacroSnapshotView,
    MarketNewsFeedView,
    NewsPreferencesView,
    RegimeBatchView,
    RegimeHistoryView,
    RegimeSnapshotView,
    TickerNewsView,
    TickerProfileBatchView,
    TickerProfileView,
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
    path("tickers/profiles/", TickerProfileBatchView.as_view(), name="ticker-profile-batch"),
    path("tickers/<str:ticker>/news/", TickerNewsView.as_view(), name="ticker-news"),
    path("tickers/<str:ticker>/sparkline/", TickerSparklineView.as_view(), name="ticker-sparkline"),
    path("tickers/<str:ticker>/profile/", TickerProfileView.as_view(), name="ticker-profile"),
    path("news/feed/", MarketNewsFeedView.as_view(), name="market-news-feed"),
    path("news/preferences/", NewsPreferencesView.as_view(), name="news-preferences"),
]
