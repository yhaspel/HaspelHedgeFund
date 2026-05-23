from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("apps.accounts.urls")),
    path("api/", include("apps.runs.urls")),
    path("api/", include("apps.data.urls")),
    path("api/", include("apps.backtests.urls")),
    path("api/", include("apps.models_catalog.urls")),
    path("api/", include("apps.portfolios.urls")),
    path("api/screener/", include("apps.screener.urls")),
]
