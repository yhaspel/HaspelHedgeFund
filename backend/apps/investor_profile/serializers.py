"""DRF serializers for the investor-profile API."""
from __future__ import annotations

from rest_framework import serializers

from .models import InvestorProfileState, QuestionnaireResponse


class QuestionnaireResponseSerializer(serializers.ModelSerializer):
    investor_type = serializers.SerializerMethodField()

    class Meta:
        model = QuestionnaireResponse
        fields = (
            "id",
            "source",
            "derived_from",
            "schema_version",
            "answers",
            "created_at",
            "model_id",
            "analysis_status",
            "analyzed_at",
            "error_message",
            "analysis",
            "profile_summary",
            "agent_brief",
            "investor_type",
        )
        read_only_fields = fields

    def get_investor_type(self, obj: QuestionnaireResponse) -> str:
        return (obj.analysis or {}).get("investor_type", "") if obj.analysis else ""


class QuestionnaireHistoryItemSerializer(serializers.ModelSerializer):
    investor_type = serializers.SerializerMethodField()

    class Meta:
        model = QuestionnaireResponse
        fields = (
            "id",
            "source",
            "created_at",
            "model_id",
            "analysis_status",
            "investor_type",
        )
        read_only_fields = fields

    def get_investor_type(self, obj: QuestionnaireResponse) -> str:
        return (obj.analysis or {}).get("investor_type", "") if obj.analysis else ""


class InvestorProfileStateSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvestorProfileState
        fields = (
            "apply_to_runs",
            "nudge_dismiss_count",
            "nudge_last_dismissed_at",
            "updated_at",
        )
        read_only_fields = (
            "nudge_dismiss_count",
            "nudge_last_dismissed_at",
            "updated_at",
        )
