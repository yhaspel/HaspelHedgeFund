from django.contrib import admin

from .models import ScheduledRun, ScheduledRunHistory


@admin.register(ScheduledRun)
class ScheduledRunAdmin(admin.ModelAdmin):
    list_display = (
        "name", "user", "cron_expression", "timezone",
        "is_active", "next_run_at", "last_run_at",
    )
    list_filter = ("is_active", "is_market_aware", "on_breach")
    search_fields = ("name", "user__email")


@admin.register(ScheduledRunHistory)
class ScheduledRunHistoryAdmin(admin.ModelAdmin):
    list_display = (
        "scheduled_run", "fire_time_utc", "status",
        "notified_count", "estimated_cost_usd", "actual_cost_usd",
    )
    list_filter = ("status",)
