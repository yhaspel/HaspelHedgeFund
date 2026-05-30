from django.urls import path

from .views import (
    WatchlistDetailView,
    WatchlistListCreateView,
    WatchlistTickerAddView,
    WatchlistTickerDeleteView,
)

urlpatterns = [
    path("", WatchlistListCreateView.as_view(), name="watchlist-list"),
    path("<str:ref>/", WatchlistDetailView.as_view(), name="watchlist-detail"),
    path(
        "<str:ref>/tickers/",
        WatchlistTickerAddView.as_view(),
        name="watchlist-ticker-add",
    ),
    path(
        "<str:ref>/tickers/<str:ticker>/",
        WatchlistTickerDeleteView.as_view(),
        name="watchlist-ticker-delete",
    ),
]
