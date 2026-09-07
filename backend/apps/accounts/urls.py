from django.urls import path

from .views import (
    CostSummaryView,
    HealthView,
    LogoutView,
    MeView,
    SignupView,
    ThrottledTokenObtainPairView,
    ThrottledTokenRefreshView,
)

urlpatterns = [
    path("auth/signup/", SignupView.as_view(), name="signup"),
    path("auth/login/", ThrottledTokenObtainPairView.as_view(), name="login"),
    path("auth/refresh/", ThrottledTokenRefreshView.as_view(), name="refresh"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("me/", MeView.as_view(), name="me"),
    path("health/", HealthView.as_view(), name="health"),
    path("costs/summary/", CostSummaryView.as_view(), name="cost-summary"),
]
