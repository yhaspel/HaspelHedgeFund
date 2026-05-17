from rest_framework import serializers

from hedgefund_agents.models import LLMCall

from .models import AgentMessage, Decision, Run


class LLMCallSerializer(serializers.ModelSerializer):
    class Meta:
        model = LLMCall
        fields = (
            "id", "agent_name", "provider", "model",
            "prompt_tokens", "cached_tokens", "completion_tokens",
            "cost_usd", "latency_ms", "created_at",
        )


class AgentMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentMessage
        fields = ("id", "agent_name", "parsed_output", "status", "created_at")


class DecisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Decision
        fields = (
            "id", "ticker", "action", "confidence",
            "rationale", "dissenting_views",
            "target_quantity", "target_weight_pct", "risk_overrides",
            "created_at",
        )


class RunDetailSerializer(serializers.ModelSerializer):
    messages = AgentMessageSerializer(many=True, read_only=True)
    decisions = DecisionSerializer(many=True, read_only=True)
    llm_calls = LLMCallSerializer(many=True, read_only=True)

    class Meta:
        model = Run
        fields = (
            "id", "tickers", "status", "model_overrides", "as_of_date",
            "personas", "agent_versions",
            "created_at", "finished_at", "total_cost_usd", "error_message",
            "messages", "decisions", "llm_calls",
        )


class RunListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Run
        fields = (
            "id", "tickers", "status", "as_of_date",
            "created_at", "finished_at", "total_cost_usd",
        )


class RunCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Run
        fields = ("id", "tickers", "model_overrides", "as_of_date", "personas", "status")
        read_only_fields = ("id", "status")

    def validate_tickers(self, v: list) -> list:
        if not v or not isinstance(v, list):
            raise serializers.ValidationError("tickers must be a non-empty list")
        return [str(t).upper() for t in v]
