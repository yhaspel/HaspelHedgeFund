import re

from rest_framework import serializers

from apps.graphs.models import AgentGraphVersion
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
            "side", "target_weight_signed",
            "created_at",
        )


class DecisionSummarySerializer(serializers.ModelSerializer):
    """Compact decision form for the Runs list — just enough to render a pill."""

    class Meta:
        model = Decision
        fields = ("id", "ticker", "action", "side", "confidence")


def _strategy_backlink(run: Run) -> dict | None:
    """Return strategy/cycle metadata for a strategy-sourced run.

    Returns None for ad-hoc runs. Resolved lazily to avoid extra joins
    when the field isn't relevant.
    """
    if run.source != Run.STRATEGY or run.portfolio_target_id is None:
        return None
    target = run.portfolio_target
    if target is None:
        return None
    return {
        "portfolio_target_id": target.id,
        "portfolio_target_status": target.status,
        "as_of_date": target.as_of_date.isoformat() if target.as_of_date else None,
        "strategy_id": target.strategy_id,
        "strategy_name": target.strategy.name if target.strategy_id else "",
        "strategy_kind": target.strategy.kind if target.strategy_id else "",
    }


class RunDetailSerializer(serializers.ModelSerializer):
    messages = AgentMessageSerializer(many=True, read_only=True)
    decisions = DecisionSerializer(many=True, read_only=True)
    llm_calls = LLMCallSerializer(many=True, read_only=True)
    portfolio_target = serializers.IntegerField(source="portfolio_target_id", read_only=True)
    strategy_backlink = serializers.SerializerMethodField()
    rerun_of = serializers.IntegerField(source="rerun_of_id", read_only=True)
    reruns = serializers.SerializerMethodField()

    class Meta:
        model = Run
        fields = (
            "id", "tickers", "status", "model_overrides", "as_of_date",
            "personas", "agent_versions",
            "created_at", "finished_at", "total_cost_usd", "error_message",
            "source", "portfolio_target", "strategy_backlink",
            # P4 WS-A: rerun provenance (this run's parent + its child reruns).
            "rerun_of", "reruns",
            "messages", "decisions", "llm_calls",
            # P01/P02a review: surface evidence and risk-context to the UI.
            "evidence", "risk_context",
            # P3-prereq-5 WS-C: investor-profile audit snapshot for the badge.
            "investor_profile_applied",
            # P3-D WS-D: persona-evolution audit snapshot for the Evolved badge.
            "persona_evolution_applied",
        )

    def get_strategy_backlink(self, run: Run) -> dict | None:
        return _strategy_backlink(run)

    def get_reruns(self, run: Run) -> list[int]:
        # Child reruns of this run, oldest first. Uses the reverse FK accessor.
        return list(run.reruns.order_by("created_at").values_list("id", flat=True))


class RunListSerializer(serializers.ModelSerializer):
    portfolio_target = serializers.IntegerField(source="portfolio_target_id", read_only=True)
    strategy_backlink = serializers.SerializerMethodField()
    decisions = DecisionSummarySerializer(many=True, read_only=True)
    rerun_of = serializers.IntegerField(source="rerun_of_id", read_only=True)

    class Meta:
        model = Run
        fields = (
            "id", "tickers", "status", "as_of_date",
            "created_at", "finished_at", "total_cost_usd",
            "source", "portfolio_target", "strategy_backlink",
            "personas",  # P3 prereq 2 / WS-1: Runs list shows persona count.
            "rerun_of",  # P4 WS-A: "Rerun of #N" provenance pill.
            "decisions",
        )

    def get_strategy_backlink(self, run: Run) -> dict | None:
        return _strategy_backlink(run)


class RunCreateSerializer(serializers.ModelSerializer):
    # P4c: optional agent-graph version to run on. When set, its node models
    # flatten into model_overrides and its personas become the persona subset.
    graph_version_id = serializers.PrimaryKeyRelatedField(
        source="graph_version", required=False, allow_null=True,
        queryset=AgentGraphVersion.objects.all(),
    )

    class Meta:
        model = Run
        fields = ("id", "tickers", "model_overrides", "as_of_date", "personas",
                  "status", "graph_version_id")
        read_only_fields = ("id", "status")

    def validate(self, attrs: dict) -> dict:
        version = attrs.get("graph_version")
        if version is not None:
            from apps.graphs.submission import apply_graph_version, check_submittable

            user = getattr(self.context.get("request"), "user", None)
            check_submittable(version, user)
            apply_graph_version(attrs, version, user=user)
        return attrs

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

        # P01 review: reject typoed agent override keys against the canonical
        # registry so a stray "fundamentls" key isn't silently ignored.
        from apps.models_catalog.presets import ALL_AGENTS

        unknown_agents = [k for k in v if str(k) not in ALL_AGENTS]
        if unknown_agents:
            raise serializers.ValidationError(
                f"model_overrides has unknown agent key(s): {sorted(unknown_agents)}. "
                f"Known agents: {sorted(ALL_AGENTS)}"
            )

        try:
            from django.db.models import Q

            from apps.models_catalog.models import ModelEntry, ProviderKey
            from apps.models_catalog.ollama_discovery import discover_ollama_models
        except Exception:
            return v

        # P3-C §12.2 (Gap F): split overrides by provider prefix. ollama:
        # ids are validated against the requesting user's live discovery —
        # they are intentionally per-user/ephemeral and have no ModelEntry
        # row. Everything else validates against the catalog.
        ollama_overrides: dict[str, str] = {}
        catalog_overrides: dict[str, str] = {}
        for agent, mid in v.items():
            if str(mid).startswith("ollama:"):
                ollama_overrides[agent] = str(mid)
            else:
                catalog_overrides[agent] = str(mid)

        if ollama_overrides:
            user = getattr(self.context.get("request"), "user", None)
            host = ""
            if user is not None and getattr(user, "is_authenticated", False):
                pk = ProviderKey.objects.filter(user=user).first()
                host = pk.ollama_host if pk else ""
            discovered = {m["id"] for m in discover_ollama_models(host)}
            for agent, mid in ollama_overrides.items():
                if mid not in discovered:
                    raise serializers.ValidationError(
                        f"model_overrides[{agent!r}] = {mid!r} is not a known active model"
                    )

        if catalog_overrides:
            qualified = {mid for mid in catalog_overrides.values()}
            bare = {mid.split(":", 1)[-1] for mid in qualified}
            known = set(
                ModelEntry.objects.filter(is_active=True)
                .filter(Q(id__in=qualified) | Q(id__in=bare))
                .values_list("id", flat=True)
            )
            known_short = {k.split(":", 1)[-1] for k in known}
            for agent, mid in catalog_overrides.items():
                short = mid.split(":", 1)[-1]
                if mid not in known and short not in known_short:
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
