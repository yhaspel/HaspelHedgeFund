from django.urls import path

from .views import (
    GraphDetailView,
    GraphFromTemplateView,
    GraphListCreateView,
    GraphRegistryView,
    GraphValidateView,
    GraphVersionDetailView,
    GraphVersionListCreateView,
)

urlpatterns = [
    # Static / specific routes before <int:pk> (int converter already excludes
    # non-numeric, but keep them first for clarity).
    path("graphs/registry/", GraphRegistryView.as_view(), name="graph-registry"),
    path("graphs/validate/", GraphValidateView.as_view(), name="graph-validate"),
    path("graphs/from-template/<int:template_id>/", GraphFromTemplateView.as_view(),
         name="graph-from-template"),
    path("graphs/", GraphListCreateView.as_view(), name="graph-list-create"),
    path("graphs/<int:pk>/", GraphDetailView.as_view(), name="graph-detail"),
    path("graphs/<int:graph_id>/versions/", GraphVersionListCreateView.as_view(),
         name="graph-version-list-create"),
    path("graphs/<int:graph_id>/versions/<int:version>/", GraphVersionDetailView.as_view(),
         name="graph-version-detail"),
]
