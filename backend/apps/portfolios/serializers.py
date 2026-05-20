from __future__ import annotations

from rest_framework import serializers

from .models import (
    BorrowQuote,
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
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
        fields = ("id", "name", "cash_balance", "created_at")
        read_only_fields = ("created_at",)


class PositionSerializer(serializers.ModelSerializer):
    is_short = serializers.BooleanField(read_only=True)

    class Meta:
        model = Position
        fields = ("id", "ticker", "quantity", "avg_cost", "sector", "is_short", "opened_at")


class StrategySerializer(serializers.ModelSerializer):
    universe_name = serializers.CharField(source="universe.name", read_only=True)
    portfolio_name = serializers.CharField(source="portfolio.name", read_only=True)

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
        return portfolio


class RebalanceOrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = RebalanceOrder
        fields = ("id", "ticker", "side", "quantity", "limit_price",
                  "reason", "estimated_notional_usd", "sequence")


class ScreenerRankingSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScreenerRanking
        fields = ("id", "as_of_date", "long_candidates", "short_candidates",
                  "universe_size_evaluated", "created_at")


class PortfolioTargetSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = PortfolioTarget
        fields = ("id", "as_of_date", "status", "gross_pct", "net_pct",
                  "realised_net_pct", "realised_portfolio_beta",
                  "total_cost_usd", "created_at", "finished_at")


class PortfolioTargetDetailSerializer(serializers.ModelSerializer):
    orders = RebalanceOrderSerializer(many=True, read_only=True)
    screener_ranking = ScreenerRankingSerializer(read_only=True)

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
            "total_cost_usd", "error_message",
            "created_at", "finished_at",
        )


class BorrowQuoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = BorrowQuote
        fields = ("ticker", "as_of_date", "is_locatable", "fee_pct_annual", "source")
