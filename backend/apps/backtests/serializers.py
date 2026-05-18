from rest_framework import serializers

from .models import Backtest, BacktestFold, BacktestMetrics


class BacktestMetricsSerializer(serializers.ModelSerializer):
    class Meta:
        model = BacktestMetrics
        exclude = ("id", "backtest")


class BacktestFoldSerializer(serializers.ModelSerializer):
    class Meta:
        model = BacktestFold
        fields = (
            "id", "fold_index", "is_start", "is_end", "oos_start", "oos_end",
            "winning_config", "is_sharpe", "oos_sharpe", "oos_return_pct",
            "oos_max_drawdown_pct",
        )


class BacktestListSerializer(serializers.ModelSerializer):
    oos_sharpe = serializers.SerializerMethodField()
    total_return_pct = serializers.SerializerMethodField()
    deflation = serializers.SerializerMethodField()

    class Meta:
        model = Backtest
        fields = (
            "id", "name", "status", "progress_pct", "progress_message",
            "universe", "start_date", "end_date", "created_at",
            "finished_at", "oos_sharpe", "total_return_pct", "deflation",
        )

    def _m(self, obj):
        return getattr(obj, "metrics", None)

    def get_oos_sharpe(self, obj):
        m = self._m(obj)
        return float(m.mean_oos_sharpe) if m else None

    def get_total_return_pct(self, obj):
        m = self._m(obj)
        return float(m.total_return_pct) if m else None

    def get_deflation(self, obj):
        m = self._m(obj)
        return float(m.sharpe_deflation) if m else None


class BacktestDetailSerializer(serializers.ModelSerializer):
    metrics = BacktestMetricsSerializer(read_only=True)
    folds = BacktestFoldSerializer(many=True, read_only=True)

    class Meta:
        model = Backtest
        fields = (
            "id", "name", "universe", "start_date", "end_date", "starting_cash",
            "commission_bps", "spread_bps", "agent_graph_version", "agent_versions",
            "model_overrides", "personas", "rebalance_frequency",
            "is_window_days", "oos_window_days", "step_days",
            "search_space", "n_candidates", "is_objective", "rng_seed", "baseline",
            "status", "progress_pct", "progress_message", "error_message",
            "total_cost_usd", "created_at", "started_at", "finished_at",
            "metrics", "folds",
        )

    def to_representation(self, instance):
        d = super().to_representation(instance)
        d["total_cost_usd"] = float(d.get("total_cost_usd") or 0)
        return d


DEFAULT_UNIVERSE_20 = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK.B",
    "JPM", "V", "JNJ", "WMT", "PG", "MA", "HD", "XOM", "CVX", "KO",
    "PEP", "ABBV",
]


class BacktestCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Backtest
        fields = (
            "id", "name", "universe", "start_date", "end_date", "starting_cash",
            "commission_bps", "spread_bps", "personas", "model_overrides",
            "rebalance_frequency", "is_window_days", "oos_window_days", "step_days",
            "search_space", "n_candidates", "is_objective", "rng_seed", "baseline",
            "status",
        )
        read_only_fields = ("id", "status")

    def validate_universe(self, v):
        if not v:
            return DEFAULT_UNIVERSE_20
        return [str(t).upper() for t in v]

    def validate(self, attrs):
        if attrs["end_date"] <= attrs["start_date"]:
            raise serializers.ValidationError("end_date must be after start_date")
        if attrs.get("is_window_days", 252) < 126:
            raise serializers.ValidationError("is_window_days must be >= 126 (6 months)")
        master_days = (attrs["end_date"] - attrs["start_date"]).days
        if master_days < attrs.get("is_window_days", 252) + attrs.get("oos_window_days", 63):
            raise serializers.ValidationError(
                "master window too short for one fold (need >= is_window + oos_window days)"
            )
        return attrs
