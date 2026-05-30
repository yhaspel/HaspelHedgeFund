from django.urls import path

from .views import (
    ScheduledRunDetailView,
    ScheduledRunHistoryView,
    ScheduledRunListCreateView,
    ScheduledRunRunNowView,
)

urlpatterns = [
    path("scheduled-runs/", ScheduledRunListCreateView.as_view(), name="scheduled-run-list"),
    path("scheduled-runs/<int:pk>/", ScheduledRunDetailView.as_view(), name="scheduled-run-detail"),
    path(
        "scheduled-runs/<int:pk>/history/",
        ScheduledRunHistoryView.as_view(),
        name="scheduled-run-history",
    ),
    path(
        "scheduled-runs/<int:pk>/run-now/",
        ScheduledRunRunNowView.as_view(),
        name="scheduled-run-run-now",
    ),
]
