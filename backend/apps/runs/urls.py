from django.urls import path

from .views import (
    ProviderDiagnosticsView,
    RunCancelView,
    RunDetailView,
    RunListCreateView,
)

urlpatterns = [
    path("runs/", RunListCreateView.as_view(), name="run-list-create"),
    path("runs/<int:pk>/", RunDetailView.as_view(), name="run-detail"),
    path("runs/<int:pk>/cancel/", RunCancelView.as_view(), name="run-cancel"),
    # P01/P02b review: operator-facing provider diagnostics.
    path("diagnostics/providers/", ProviderDiagnosticsView.as_view(),
         name="provider-diagnostics"),
]
