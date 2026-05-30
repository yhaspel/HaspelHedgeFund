from __future__ import annotations

from rest_framework import serializers

from .models import (
    BorrowQuote,
    LedgerEntry,
    Portfolio,
    PortfolioPreferences,
    PortfolioStrategy,
    PortfolioTarget,
    PortfolioTargetRun,
    Position,
    RebalanceOrder,
    ScreenerRanking,
    Universe,
    UniverseMembership,
)


class UniverseSerializer(serializers.ModelSerializer):
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = Universe
        fields = ("id", "name", "description", "source", "is_active", "member_count")

    def get_member_count(self, obj) -> int:
        return obj.memberships.count()


class UniverseMembershipSerializer(serializers.ModelSerializer):
    class Meta:
        model = UniverseMembership
        fields = ("ticker", "sector", "effective_from", "effective_to")


class PortfolioSerializer(serializers.ModelSerializer):
    class Meta:
        model = Portfolio
        fields = ("id", "name", "kind", "cash_balance", "created_at")
        read_only_fields = ("kind", "created_at")


class PositionSerializer(serializers.ModelSerializer):
    is_short = serializers.BooleanField(read_only=True)

    class Meta:
        model = Position
        fields = (
            "id", "ticker", "quantity", "avg_cost", "sector", "is_short",
            "opened_at", "opened_via", "source_run", "source_decision",
            "note", "realized_pnl",
        )


class PositionValuationSerializer(serializers.Serializer):
    """P3: position row + mark-to-market columns from valuation.value_portfolio."""
    id = serializers.IntegerField()
    ticker = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=18, decimal_places=6)
    avg_cost = serializers.DecimalField(max_digits=12, decimal_places=4)
    is_short = serializers.BooleanField()
    sector = serializers.CharField(allow_blank=True)
    opened_at = serializers.DateTimeField()
    opened_via = serializers.CharField()
    source_run_id = serializers.IntegerField(allow_null=True)
    source_decision_id = serializers.IntegerField(allow_null=True)
    note = serializers.CharField(allow_blank=True)
    realized_pnl = serializers.DecimalField(max_digits=14, decimal_places=2)
    mark_price = serializers.DecimalField(
        max_digits=18, decimal_places=4, allow_null=True,
    )
    mark_as_of = serializers.DateField(allow_null=True)
    mark_stale = serializers.BooleanField()
    market_value = serializers.DecimalField(max_digits=14, decimal_places=2)
    unrealized_pnl = serializers.DecimalField(max_digits=14, decimal_places=2)
    unrealized_pnl_pct = serializers.DecimalField(
        max_digits=10, decimal_places=2, allow_null=True,
    )
    weight_pct = serializers.DecimalField(max_digits=10, decimal_places=2)
    warnings = serializers.ListField(child=serializers.CharField())


class PortfolioPreferencesViewSerializer(serializers.Serializer):
    """P3: portfolio preferences as embedded into the overview payload."""
    mark_cadence = serializers.CharField()
    interval_minutes = serializers.IntegerField()
    last_refreshed_at = serializers.DateTimeField(allow_null=True)


class PortfolioPreferencesSerializer(serializers.ModelSerializer):
    """P3: GET/PUT /api/portfolio/preferences/."""

    class Meta:
        model = PortfolioPreferences
        fields = ("mark_cadence", "interval_minutes", "last_refreshed_at")
        read_only_fields = ("last_refreshed_at",)

    def validate_interval_minutes(self, value: int) -> int:
        if value < PortfolioPreferences.MIN_INTERVAL_MINUTES:
            raise serializers.ValidationError(
                f"interval_minutes must be at least "
                f"{PortfolioPreferences.MIN_INTERVAL_MINUTES}"
            )
        if value > PortfolioPreferences.MAX_INTERVAL_MINUTES:
            raise serializers.ValidationError(
                f"interval_minutes must be at most "
                f"{PortfolioPreferences.MAX_INTERVAL_MINUTES}"
            )
        return value


class PortfolioValuationSerializer(serializers.Serializer):
    """P3: full Manual Book snapshot — cash, totals, exposures, positions."""
    portfolio_id = serializers.IntegerField()
    name = serializers.CharField()
    kind = serializers.CharField()
    cash_balance = serializers.DecimalField(max_digits=14, decimal_places=2)
    reserved_short_proceeds = serializers.DecimalField(
        max_digits=14, decimal_places=2,
    )
    free_cash = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_value = serializers.DecimalField(max_digits=14, decimal_places=2)
    long_market_value = serializers.DecimalField(max_digits=14, decimal_places=2)
    short_market_value = serializers.DecimalField(max_digits=14, decimal_places=2)
    gross_exposure_pct = serializers.DecimalField(max_digits=10, decimal_places=2)
    net_exposure_pct = serializers.DecimalField(max_digits=10, decimal_places=2)
    unrealized_pnl = serializers.DecimalField(max_digits=14, decimal_places=2)
    realized_pnl = serializers.DecimalField(max_digits=14, decimal_places=2)
    positions = PositionValuationSerializer(many=True)
    preferences = PortfolioPreferencesViewSerializer(allow_null=True)
    warnings = serializers.ListField(child=serializers.CharField())


class LedgerEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = LedgerEntry
        fields = (
            "id", "kind", "ticker", "quantity_delta", "price",
            "cash_delta", "realized_pnl",
            "quantity_after", "cash_balance_after",
            "position", "source_run", "source_decision",
            "note", "created_at",
        )
        read_only_fields = fields


class SuggestionFactorSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    effect = serializers.CharField()
    detail = serializers.CharField()


class PositionSuggestionSerializer(serializers.Serializer):
    ticker = serializers.CharField()
    side = serializers.CharField()
    suggested_weight_pct = serializers.DecimalField(
        max_digits=10, decimal_places=2,
    )
    target_notional_usd = serializers.DecimalField(
        max_digits=14, decimal_places=2,
    )
    target_quantity = serializers.DecimalField(max_digits=18, decimal_places=6)
    suggested_notional_usd = serializers.DecimalField(
        max_digits=14, decimal_places=2,
    )
    suggested_quantity = serializers.DecimalField(
        max_digits=18, decimal_places=6,
    )
    quantity_mode = serializers.CharField()
    rounding_residual_usd = serializers.DecimalField(
        max_digits=14, decimal_places=2,
    )
    current_price = serializers.DecimalField(max_digits=18, decimal_places=4)
    price_as_of = serializers.DateField(allow_null=True)
    portfolio_total_value = serializers.DecimalField(
        max_digits=14, decimal_places=2,
    )
    free_cash = serializers.DecimalField(max_digits=14, decimal_places=2)
    existing_quantity = serializers.DecimalField(
        max_digits=18, decimal_places=6,
    )
    existing_side = serializers.CharField()
    action_label = serializers.CharField()
    factors = SuggestionFactorSerializer(many=True)
    warnings = serializers.ListField(child=serializers.CharField())


class StrategySerializer(serializers.ModelSerializer):
    universe_name = serializers.CharField(source="universe.name", read_only=True)
    portfolio_name = serializers.CharField(source="portfolio.name", read_only=True)
    # P4 WS-C: count of non-cancelled cycles. Gates the Delete affordance — a
    # strategy is deletable only when this is 0. Reads a queryset annotation
    # when present (list/detail views provide it) to avoid an N+1 count.
    targets_count_active = serializers.SerializerMethodField()

    def get_targets_count_active(self, strategy) -> int:
        annotated = getattr(strategy, "targets_count_active_annotated", None)
        if annotated is not None:
            return annotated
        return strategy.targets.exclude(status="cancelled").count()

    class Meta:
        model = PortfolioStrategy
        fields = (
            "id", "name", "kind", "universe", "universe_name", "portfolio", "portfolio_name",
            "target_gross_pct", "target_net_pct",
            "max_position_pct", "max_sector_pct", "min_position_pct",
            "top_k_longs", "top_k_shorts",
            "personas", "model_preset", "cost_ceiling_per_cycle_usd",
            "min_trade_notional_usd", "max_turnover_pct",
            "screener_weights",
            "benchmark_ticker", "beta_window_days",
            "neutrality_tolerance_dollar_pct", "neutrality_tolerance_beta",
            "drop_on_unreliable_beta",
            "max_positions", "min_positions", "min_aggregate_confidence",
            "max_etfs_held", "per_etf_max_pct", "per_etf_min_pct",
            "use_sector_council_v2", "bearish_veto_threshold",
            "asset_class_caps", "prefer_inverse_etf_over_short",
            "max_inverse_etf_hold_days",
            "vol_window_days", "rebalance_band_pct", "enable_council_veto",
            "pair_entry_z", "pair_exit_z", "pair_stop_z",
            "pair_max_held", "pair_notional_pct",
            "pair_cointegration_p_max", "pair_lookback_days",
            "pair_correlation_min",
            "enable_pair_council", "pair_council_min_confidence",
            "auto_run_council", "auto_enroll_on_done",
            "targets_count_active",
            "is_active", "last_run_at", "created_at",
        )
        read_only_fields = ("last_run_at", "created_at")

    def validate_portfolio(self, portfolio: Portfolio) -> Portfolio:
        # A strategy may only point at a portfolio the requesting user owns.
        # Without this, an attacker who guesses a numeric portfolio_id could
        # attach their strategy (and its cycles/trades) to someone else's book.
        request = self.context.get("request")
        if request is None or not request.user or not request.user.is_authenticated:
            raise serializers.ValidationError("authentication required")
        if portfolio.user_id != request.user.id:
            raise serializers.ValidationError(
                "portfolio belongs to another user"
            )
        # P3/P3a-1 isolation guarantee: strategies must never operate on
        # the Manual Book or a broker-backed portfolio. Each kind has its
        # own dedicated mutation surface.
        if portfolio.kind == Portfolio.KIND_MANUAL:
            raise serializers.ValidationError(
                "the Manual Book cannot be used as a strategy portfolio"
            )
        if portfolio.kind == Portfolio.KIND_BROKER:
            raise serializers.ValidationError(
                "broker-backed portfolios cannot be used as a strategy portfolio"
            )
        return portfolio


class RebalanceOrderSerializer(serializers.ModelSerializer):
    # P02k review: surface the pair linkage so the UI / API consumer can
    # verify that paired legs share the same Pair row. Read-only.
    pair_label = serializers.SerializerMethodField()

    class Meta:
        model = RebalanceOrder
        fields = ("id", "ticker", "side", "quantity", "limit_price",
                  "reason", "estimated_notional_usd", "sequence",
                  "pair", "pair_label")
        read_only_fields = ("pair",)

    def get_pair_label(self, obj: RebalanceOrder) -> str:
        p = obj.pair
        if not p:
            return ""
        return f"{p.leg_a_ticker}/{p.leg_b_ticker}"


class ScreenerRankingSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScreenerRanking
        fields = ("id", "as_of_date", "long_candidates", "short_candidates",
                  "universe_size_evaluated", "created_at")


class PortfolioTargetSummarySerializer(serializers.ModelSerializer):
    superseded_by = serializers.IntegerField(source="superseded_by_id", read_only=True)

    class Meta:
        model = PortfolioTarget
        fields = ("id", "as_of_date", "status", "gross_pct", "net_pct",
                  "realised_net_pct", "realised_portfolio_beta",
                  "total_cost_usd", "created_at", "finished_at",
                  # P4 WS-B: rerun supersede link; P4 WS-E: enrollment marker.
                  "superseded_by", "enrolled_at")


class PortfolioTargetRunSummarySerializer(serializers.ModelSerializer):
    """P2l: one-row summary for the cycle detail's candidate-run table."""
    run_id = serializers.IntegerField(source="run.id", read_only=True)
    run_status = serializers.CharField(source="run.status", read_only=True)
    tickers = serializers.JSONField(source="run.tickers", read_only=True)
    run_cost_usd = serializers.DecimalField(
        source="run.total_cost_usd", max_digits=10, decimal_places=6, read_only=True,
    )

    class Meta:
        model = PortfolioTargetRun
        fields = (
            "run_id", "run_status", "tickers", "run_cost_usd",
            "candidate_key", "primary_ticker", "side",
            "screener_rank", "screener_score", "sector", "borrow_veto",
        )


class PortfolioTargetDetailSerializer(serializers.ModelSerializer):
    orders = RebalanceOrderSerializer(many=True, read_only=True)
    screener_ranking = ScreenerRankingSerializer(read_only=True)
    candidate_runs = serializers.SerializerMethodField()
    superseded_by = serializers.IntegerField(source="superseded_by_id", read_only=True)

    class Meta:
        model = PortfolioTarget
        fields = (
            "id", "as_of_date", "status",
            "target_weights", "gross_pct", "net_pct",
            "realised_net_pct", "realised_portfolio_beta", "beta_diagnostics",
            "per_position_thesis", "cycle_outcome",
            "sector_exposure",
            "rejected_candidates", "decisions", "sector_veto_log",
            "screener_ranking", "orders",
            "candidate_runs",
            # P3 addendum: cycle-level mark-to-market snapshot.
            "marked_snapshot",
            "total_cost_usd", "error_message",
            # P4 WS-B/WS-E: rerun supersede link + enrollment marker.
            "superseded_by", "enrolled_at",
            "created_at", "finished_at",
        )

    def get_candidate_runs(self, target: PortfolioTarget) -> list[dict]:
        links = (
            PortfolioTargetRun.objects.filter(target=target)
            .select_related("run")
            .order_by("screener_rank", "candidate_key")
        )
        return PortfolioTargetRunSummarySerializer(links, many=True).data


class BorrowQuoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = BorrowQuote
        fields = ("ticker", "as_of_date", "is_locatable", "fee_pct_annual", "source")
