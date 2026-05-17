from django.urls import path

from .views import MacroSnapshotView, TickerNewsView

urlpatterns = [
    path("macro/snapshot/", MacroSnapshotView.as_view(), name="macro-snapshot"),
    path("tickers/<str:ticker>/news/", TickerNewsView.as_view(), name="ticker-news"),
]
