import re

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

    # P1 single-ticker constraint: per-run agent state isn't ticker-keyed yet,
    # so allowing multiple tickers here would silently merge their analyses
    # under one set of AgentMessages. Multi-ticker fan-out is P2e/strategies.
    TICKERS_MAX = 1
    TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,15}$")

    def validate_tickers(self, v: list) -> list:
        if not v or not isinstance(v, list):
            raise serializers.ValidationError("tickers must be a non-empty list")
        if len(v) > self.TICKERS_MAX:
            raise serializers.ValidationError(
                f"tickers may contain at most {self.TICKERS_MAX} symbol; "
                "use a PortfolioStrategy for multi-ticker workflows"
            )
        normalized = [str(t).strip().upper() for t in v]
        if any(not t for t in normalized):
            raise serializers.ValidationError("tickers contains an empty entry")
        if len(set(normalized)) != len(normalized):
            raise serializers.ValidationError("tickers contains duplicate symbols")
        for t in normalized:
            if not self.TICKER_RE.match(t):
                raise serializers.ValidationError(
                    f"ticker {t!r} is not a valid symbol (A-Z, 0-9, '.', '-'; max 16 chars)"
                )
        return normalized

    def validate_model_overrides(self, v: dict) -> dict:
        # Reject unknown / inactive models or unreachable providers at the API
        # boundary so the user doesn't wait for Celery to discover it.
        if not v:
            return v or {}
        if not isinstance(v, dict):
            raise serializers.ValidationError("model_overrides must be an object")
        try:
            from django.db.models import Q

            from apps.models_catalog.models import ModelEntry
        except Exception:
            return v
        qualified = {str(mid) for mid in v.values()}
        bare = {mid.split(":", 1)[-1] for mid in qualified}
        known = set(
            ModelEntry.objects.filter(is_active=True)
            .filter(Q(id__in=qualified) | Q(id__in=bare))
            .values_list("id", flat=True)
        )
        known_short = {k.split(":", 1)[-1] for k in known}
        for agent, mid in v.items():
            short = str(mid).split(":", 1)[-1]
            if str(mid) not in known and short not in known_short:
                raise serializers.ValidationError(
                    f"model_overrides[{agent!r}] = {mid!r} is not a known active model"
                )
        return v

    def validate_personas(self, v: list) -> list:
        # Reject unknown persona ids at the API boundary so the user gets a
        # synchronous 400 instead of a Celery task that fails 30s later inside
        # build_council_graph.
        if not v:
            return []
        if not isinstance(v, list):
            raise serializers.ValidationError("personas must be a list of agent names")
        from hedgefund_agents.personas import ALL_PERSONAS

        known = set(ALL_PERSONAS)
        normalized = [str(p).strip().lower() for p in v]
        if any(not p for p in normalized):
            raise serializers.ValidationError("personas contains an empty entry")
        if len(set(normalized)) != len(normalized):
            raise serializers.ValidationError("personas contains duplicates")
        unknown = [p for p in normalized if p not in known]
        if unknown:
            raise serializers.ValidationError(
                f"unknown personas: {unknown}. Known: {sorted(known)}"
            )
        return normalized
