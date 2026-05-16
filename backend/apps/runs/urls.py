from django.urls import path

from .views import ModelCatalogView, RunDetailView, RunListCreateView

urlpatterns = [
    path("runs/", RunListCreateView.as_view(), name="run-list-create"),
    path("runs/<int:pk>/", RunDetailView.as_view(), name="run-detail"),
    path("models/", ModelCatalogView.as_view(), name="model-catalog"),
]
