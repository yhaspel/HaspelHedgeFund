from django.urls import path

from .views import (
    AgentsView,
    ModelsView,
    MyModelPreferencesView,
    MyProviderKeysView,
    PresetView,
)

urlpatterns = [
    path("models/", ModelsView.as_view(), name="models-catalog"),
    path("agents/", AgentsView.as_view(), name="agents-list"),
    path("presets/<str:name>/", PresetView.as_view(), name="preset-detail"),
    path("me/model-preferences/", MyModelPreferencesView.as_view(), name="my-model-prefs"),
    path("me/provider-keys/", MyProviderKeysView.as_view(), name="my-provider-keys"),
]
