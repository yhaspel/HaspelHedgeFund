from django.urls import path

from .views import (
    ProfileBundleView,
    ProfileNudgeDismissView,
    ProfileStateView,
    QuestionnaireDetailView,
    QuestionnaireHistoryView,
    QuestionnaireSchemaView,
    QuestionnaireSubmitView,
    QuestionnaireTuneView,
)

urlpatterns = [
    path("profile/", ProfileBundleView.as_view(), name="profile-bundle"),
    path(
        "profile/questionnaire/",
        QuestionnaireSubmitView.as_view(),
        name="profile-questionnaire-submit",
    ),
    path(
        "profile/questionnaire/schema/",
        QuestionnaireSchemaView.as_view(),
        name="profile-questionnaire-schema",
    ),
    path(
        "profile/questionnaire/history/",
        QuestionnaireHistoryView.as_view(),
        name="profile-questionnaire-history",
    ),
    path(
        "profile/questionnaire/<int:pk>/",
        QuestionnaireDetailView.as_view(),
        name="profile-questionnaire-detail",
    ),
    path(
        "profile/questionnaire/<int:pk>/tune/",
        QuestionnaireTuneView.as_view(),
        name="profile-questionnaire-tune",
    ),
    path("profile/state/", ProfileStateView.as_view(), name="profile-state"),
    path(
        "profile/nudge/dismiss/",
        ProfileNudgeDismissView.as_view(),
        name="profile-nudge-dismiss",
    ),
]
