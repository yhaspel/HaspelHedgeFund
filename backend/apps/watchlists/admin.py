from django.contrib import admin

from .models import Watchlist, WatchlistTicker


class WatchlistTickerInline(admin.TabularInline):
    model = WatchlistTicker
    extra = 0


@admin.register(Watchlist)
class WatchlistAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "is_default", "created_at")
    list_filter = ("is_default",)
    search_fields = ("name", "user__email")
    inlines = [WatchlistTickerInline]
