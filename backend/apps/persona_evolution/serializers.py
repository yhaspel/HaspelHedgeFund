"""DRF serializers for the persona-evolution API (P3-D WS-F)."""
from __future__ import annotations

from rest_framework import serializers

from .models import (
    PersonaEvolutionProfile,
    PersonaEvolutionRevision,
    PersonaEvolutionSettings,
)


class PersonaEvolutionSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = PersonaEvolutionSettings
        fields = (
            "enabled",
            "cadence",
            "model_id",
            "web_search_enabled",
            "monthly_cost_cap_usd",
            "cost_cap_reached_at",
            "updated_at",
        )
        read_only_fields = ("cost_cap_reached_at", "updated_at")


class PersonaEvolutionRevisionSerializer(serializers.ModelSerializer):
    composite_markdown = serializers.SerializerMethodField()

    class Meta:
        model = PersonaEvolutionRevision
        fields = (
            "id",
            "seq",
            "as_of_date",
            "market_stance_md",
            "general_notes_md",
            "composite_markdown",
            "char_count",
            "over_budget",
            "material_change",
            "dropped_facts",
            "source_urls",
            "model_id",
            "created_at",
        )
        read_only_fields = fields

    def get_composite_markdown(self, obj: PersonaEvolutionRevision) -> str:
        return obj.composite_markdown()


class PersonaEvolutionProfileSerializer(serializers.ModelSerializer):
    current_revision = PersonaEvolutionRevisionSerializer(read_only=True)

    class Meta:
        model = PersonaEvolutionProfile
        fields = (
            "persona_name",
            "display_name",
            "firm_name",
            "is_evolvable",
            "lifecycle_note",
            "search_aliases",
            "last_cycle_at",
            "last_cycle_status",
            "last_cycle_note",
            "current_cycle_started_at",
            "current_revision",
            "updated_at",
        )
        read_only_fields = fields
