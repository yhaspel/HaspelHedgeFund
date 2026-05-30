from django.contrib import admin

from .models import NotificationChannel, NotificationEvent


@admin.register(NotificationChannel)
class NotificationChannelAdmin(admin.ModelAdmin):
    list_display = ("label", "kind", "user", "is_active", "created_at")
    list_filter = ("kind", "is_active")
    search_fields = ("name", "user__email")


@admin.register(NotificationEvent)
class NotificationEventAdmin(admin.ModelAdmin):
    list_display = ("subject", "channel", "delivery_status", "sent_at", "created_at")
    list_filter = ("delivery_status",)
    search_fields = ("subject",)
