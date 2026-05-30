from django.urls import path

from .views import (
    ProviderDiagnosticsView,
    RunCancelView,
    RunDetailView,
    RunListCreateView,
    RunRerunView,
)

urlpatterns = [
    path("runs/", RunListCreateView.as_view(), name="run-list-create"),
    path("runs/<int:pk>/", RunDetailView.as_view(), name="run-detail"),
    path("runs/<int:pk>/cancel/", RunCancelView.as_view(), name="run-cancel"),
    path("runs/<int:pk>/rerun/", RunRerunView.as_view(), name="run-rerun"),
    # P01/P02b review: operator-facing provider diagnostics.
    path("diagnostics/providers/", ProviderDiagnosticsView.as_view(),
         name="provider-diagnostics"),
]
