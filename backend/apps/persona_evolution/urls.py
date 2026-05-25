from django.urls import path

from .views import (
    PersonaEvolutionProfileListView,
    PersonaEvolutionRevisionsView,
    PersonaEvolutionRunNowView,
    PersonaEvolutionSettingsView,
)

urlpatterns = [
    path(
        "persona-evolution/settings/",
        PersonaEvolutionSettingsView.as_view(),
        name="persona-evolution-settings",
    ),
    path(
        "persona-evolution/profiles/",
        PersonaEvolutionProfileListView.as_view(),
        name="persona-evolution-profiles",
    ),
    path(
        "persona-evolution/profiles/<str:persona_name>/revisions/",
        PersonaEvolutionRevisionsView.as_view(),
        name="persona-evolution-revisions",
    ),
    path(
        "persona-evolution/run/",
        PersonaEvolutionRunNowView.as_view(),
        name="persona-evolution-run",
    ),
]
