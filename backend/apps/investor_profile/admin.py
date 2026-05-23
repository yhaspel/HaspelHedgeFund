from django.contrib import admin

from .models import InvestorProfileState, QuestionnaireResponse


@admin.register(QuestionnaireResponse)
class QuestionnaireResponseAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "source", "analysis_status", "model_id", "created_at")
    list_filter = ("source", "analysis_status")
    search_fields = ("user__email",)
    readonly_fields = (
        "created_at",
        "analyzed_at",
        "analysis",
        "profile_summary",
        "agent_brief",
    )


@admin.register(InvestorProfileState)
class InvestorProfileStateAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "apply_to_runs",
        "nudge_dismiss_count",
        "nudge_last_dismissed_at",
        "updated_at",
    )
    list_filter = ("apply_to_runs",)
    search_fields = ("user__email",)
