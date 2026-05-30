from django.urls import path

from .views import (
    NotificationChannelDetailView,
    NotificationChannelListCreateView,
    NotificationChannelTestView,
)

urlpatterns = [
    path(
        "notification-channels/",
        NotificationChannelListCreateView.as_view(),
        name="notification-channel-list",
    ),
    path(
        "notification-channels/<int:pk>/",
        NotificationChannelDetailView.as_view(),
        name="notification-channel-detail",
    ),
    path(
        "notification-channels/<int:pk>/test/",
        NotificationChannelTestView.as_view(),
        name="notification-channel-test",
    ),
]
