from django.urls import path

from .views import (
    AgentsView,
    FetchOpenRouterModelsView,
    ModelsView,
    MyModelPreferencesView,
    MyProviderKeysView,
    PresetView,
    TierConfigView,
    VerifyOpenRouterPricingView,
)

urlpatterns = [
    path("models/", ModelsView.as_view(), name="models-catalog"),
    path(
        "models/verify-pricing/",
        VerifyOpenRouterPricingView.as_view(),
        name="models-verify-pricing",
    ),
    path(
        "models/fetch/",
        FetchOpenRouterModelsView.as_view(),
        name="models-fetch",
    ),
    path("agents/", AgentsView.as_view(), name="agents-list"),
    path("presets/<str:name>/", PresetView.as_view(), name="preset-detail"),
    path("tiers/", TierConfigView.as_view(), name="tiers-list"),
    path("tiers/<str:name>/", TierConfigView.as_view(), name="tier-detail"),
    path("me/model-preferences/", MyModelPreferencesView.as_view(), name="my-model-prefs"),
    path("me/provider-keys/", MyProviderKeysView.as_view(), name="my-provider-keys"),
]
