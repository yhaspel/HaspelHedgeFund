from django.urls import path

from .views import (
    SavedScreenDetailView,
    SavedScreenListCreateView,
    ScreenerFieldsView,
    ScreenerPresetsView,
    ScreenerRunView,
)

urlpatterns = [
    path("fields/", ScreenerFieldsView.as_view(), name="screener-fields"),
    path("presets/", ScreenerPresetsView.as_view(), name="screener-presets"),
    path("run/", ScreenerRunView.as_view(), name="screener-run"),
    path("saved/", SavedScreenListCreateView.as_view(), name="screener-saved"),
    path(
        "saved/<int:pk>/",
        SavedScreenDetailView.as_view(),
        name="screener-saved-detail",
    ),
    # Watchlist routes moved to apps.watchlists (/api/watchlists/).
]
