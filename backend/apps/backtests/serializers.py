import re
from decimal import Decimal

from rest_framework import serializers

from apps.graphs.models import AgentGraphVersion
from apps.portfolios.models import PortfolioStrategy

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
    stitched_sharpe = serializers.SerializerMethodField()
    total_return_pct = serializers.SerializerMethodField()
    deflation = serializers.SerializerMethodField()
    # P10 §B4: False for deterministic / single-candidate runs, where the
    # OOS/IS ratio guards nothing — the UI shows "n/a" instead of a green ✓.
    deflation_meaningful = serializers.ReadOnlyField()

    class Meta:
        model = Backtest
        fields = (
            "id", "name", "status", "progress_pct", "progress_message",
            "universe", "start_date", "end_date", "created_at",
            "finished_at", "oos_sharpe", "stitched_sharpe", "total_return_pct",
            "deflation", "deflation_meaningful", "engine_mode", "engine_version",
            "data_era", "archived_at",
        )

    def _m(self, obj):
        return getattr(obj, "metrics", None)

    def get_oos_sharpe(self, obj):
        m = self._m(obj)
        return float(m.mean_oos_sharpe) if m else None

    def get_stitched_sharpe(self, obj):
        m = self._m(obj)
        return float(m.sharpe) if m else None

    def get_total_return_pct(self, obj):
        m = self._m(obj)
        return float(m.total_return_pct) if m else None

    def get_deflation(self, obj):
        m = self._m(obj)
        return float(m.sharpe_deflation) if m else None


class BacktestDetailSerializer(serializers.ModelSerializer):
    metrics = BacktestMetricsSerializer(read_only=True)
    folds = BacktestFoldSerializer(many=True, read_only=True)
    deflation_meaningful = serializers.ReadOnlyField()  # P10 §B4

    class Meta:
        model = Backtest
        fields = (
            "id", "name", "universe", "start_date", "end_date", "starting_cash",
            "commission_bps", "spread_bps", "agent_graph_version", "agent_versions",
            "model_overrides", "personas", "rebalance_frequency",
            "is_window_days", "oos_window_days", "step_days",
            "search_space", "n_candidates", "is_objective", "rng_seed", "baseline",
            "status", "progress_pct", "progress_message", "error_message",
            "total_cost_usd", "max_budget_usd", "disable_cio",
            "created_at", "started_at", "finished_at",
            "metrics", "folds", "engine_mode", "engine_version", "data_era",
            "deflation_meaningful",
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

# --- Input contract (fix B3a §1) -------------------------------------------
# Every one of these used to be unchecked at the API boundary. The engine has no
# second line of defence for most of them, so a bad POST reached a Celery worker:
#   * step_days <= 0 made generate_folds() loop forever, pinning a prefork slot
#     with an ever-growing folds list until the worker OOM'd;
#   * starting_cash == 0 crashed both engines with ZeroDivisionError mid-run;
#   * n_candidates == 0 crashed the council path with IndexError (candidates[0]);
#   * an unknown rebalance_frequency silently meant DAILY — the most expensive
#     cadence, one LLM prime per ticker-day;
#   * max_budget_usd == 0 DISABLED the spend kill-switch entirely;
#   * step_days < oos_window_days produced overlapping OOS folds, which the
#     stitched-curve code cannot represent (it flat-lines the overlap).
MIN_IS_WINDOW_DAYS = 126      # 6 months — enough IS history to fit anything
MIN_OOS_WINDOW_DAYS = 21      # 1 month — below this a "fold" is noise
MIN_BUDGET_USD = Decimal("0.05")
MIN_STARTING_CASH = Decimal("0.01")
MAX_UNIVERSE_SIZE = 200
REBALANCE_FREQUENCIES = ("daily", "weekly", "monthly")
# Tickers as the bar providers spell them: AAPL, BRK.B, RDS-A, plus digits.
TICKER_RE = re.compile(r"^[A-Z0-9]{1,10}(?:[.\-][A-Z0-9]{1,4})?$")


class BacktestCreateSerializer(serializers.ModelSerializer):
    # Explicit bounds — DRF renders each as a 400 naming the field. `required`
    # stays False everywhere so an omitted key still falls through to the model
    # default (the pre-existing contract).
    step_days = serializers.IntegerField(required=False, min_value=1)
    oos_window_days = serializers.IntegerField(
        required=False, min_value=MIN_OOS_WINDOW_DAYS
    )
    is_window_days = serializers.IntegerField(
        required=False, min_value=MIN_IS_WINDOW_DAYS
    )
    n_candidates = serializers.IntegerField(required=False, min_value=1)
    starting_cash = serializers.DecimalField(
        required=False, max_digits=18, decimal_places=2, min_value=MIN_STARTING_CASH,
    )
    commission_bps = serializers.DecimalField(
        required=False, max_digits=6, decimal_places=2, min_value=Decimal("0"),
    )
    spread_bps = serializers.DecimalField(
        required=False, max_digits=6, decimal_places=2, min_value=Decimal("0"),
    )
    max_budget_usd = serializers.DecimalField(
        required=False, max_digits=8, decimal_places=2, min_value=MIN_BUDGET_USD,
    )
    rebalance_frequency = serializers.ChoiceField(
        required=False, choices=REBALANCE_FREQUENCIES,
    )
    # P4c: optional agent-graph version (same semantics as Run).
    graph_version_id = serializers.PrimaryKeyRelatedField(
        source="graph_version", required=False, allow_null=True,
        queryset=AgentGraphVersion.objects.all(),
    )
    # P7 §9: optional strategy this backtest validates. When set, completing the
    # backtest unlocks that strategy's autopilot enable gate (validation.py reads
    # the strategy's latest DONE backtest). Ownership is enforced in validate().
    strategy_id = serializers.PrimaryKeyRelatedField(
        source="strategy", required=False, allow_null=True,
        queryset=PortfolioStrategy.objects.all(),
    )

    class Meta:
        model = Backtest
        fields = (
            "id", "name", "universe", "start_date", "end_date", "starting_cash",
            "commission_bps", "spread_bps", "personas", "model_overrides",
            "rebalance_frequency", "is_window_days", "oos_window_days", "step_days",
            "search_space", "n_candidates", "is_objective", "rng_seed", "baseline",
            "max_budget_usd", "disable_cio", "status", "graph_version_id",
            "strategy_id", "engine_mode",
        )
        read_only_fields = ("id", "status")

    def validate_universe(self, v):
        """Uppercase, de-duplicate (order-preserving) and shape-check tickers.

        An omitted/empty universe still falls back to the documented default 20
        (the UI relies on it); anything the caller DID supply must be a list of
        well-formed ticker symbols, because a junk symbol silently contributes
        no bars and is dropped from the run without a warning."""
        if v in (None, "", [], {}):
            return list(DEFAULT_UNIVERSE_20)
        if not isinstance(v, list):
            raise serializers.ValidationError("universe must be a list of tickers")
        out: list[str] = []
        seen: set[str] = set()
        for raw in v:
            if not isinstance(raw, str):
                raise serializers.ValidationError(f"not a ticker symbol: {raw!r}")
            t = raw.strip().upper()
            if not t:
                continue
            if not TICKER_RE.match(t):
                raise serializers.ValidationError(f"not a ticker symbol: {raw!r}")
            if t not in seen:
                seen.add(t)
                out.append(t)
        if not out:
            raise serializers.ValidationError("universe must contain at least one ticker")
        if len(out) > MAX_UNIVERSE_SIZE:
            raise serializers.ValidationError(
                f"universe has {len(out)} tickers (max {MAX_UNIVERSE_SIZE})"
            )
        return out

    def validate(self, attrs):
        if attrs["end_date"] <= attrs["start_date"]:
            raise serializers.ValidationError("end_date must be after start_date")
        # `universe` is required=False on the model (default=list): an absent key
        # never reaches validate_universe, so guarantee non-empty here too.
        if not attrs.get("universe"):
            attrs["universe"] = list(DEFAULT_UNIVERSE_20)
        # A strategy link may only point at the caller's own strategy (never leak
        # or attach across users).
        strategy = attrs.get("strategy")
        if strategy is not None:
            user = getattr(self.context.get("request"), "user", None)
            if user is None or strategy.user_id != getattr(user, "id", None):
                raise serializers.ValidationError({"strategy_id": "strategy not found"})
            # P10 §B4: kinds with no matching engine mode must not silently fall
            # through to the council engine — the run would not model how the
            # strategy trades live, yet could become §9-gate evidence.
            if strategy.kind == PortfolioStrategy.KIND_NEWS_SENTIMENT:
                raise serializers.ValidationError({
                    "strategy_id": "news_sentiment strategies can't be backtested "
                    "(no point-in-time news archive — the news history is ~weeks "
                    "deep); they validate FORWARD via the council-alpha A/B "
                    "harness instead.",
                })
            if strategy.kind == PortfolioStrategy.KIND_PAIRS:
                raise serializers.ValidationError({
                    "strategy_id": "pairs strategies have no backtest engine mode "
                    "yet; a council-engine run would not model the live pairs "
                    "cycle.",
                })
            # Deterministic strategy kinds validate on the matching backtest engine
            # so the run models how the strategy actually trades live: each routes
            # to the deterministic engine + the same sizing the live cycle uses
            # (unless the caller pinned engine_mode explicitly).
            if not attrs.get("engine_mode"):
                caller_ss = attrs.get("search_space") or {}
                # P7c Part D — leverage + regime-gate config, threaded into every
                # deterministic backtest so the run is faithful to the live cycle.
                pd_cfg = {
                    "rp_vol_target_annual": float(strategy.rp_vol_target_annual or 0),
                    # max_gross is the leverage cap AND the engine's execute gross
                    # cap; for momentum kinds the kind config overrides it below.
                    "max_gross": float(strategy.rp_max_gross or 1.0),
                    "enable_spy_regime_gate": bool(strategy.enable_spy_regime_gate),
                    "regime_gate_floor": float(strategy.regime_gate_floor),
                }
                if strategy.kind == PortfolioStrategy.KIND_RISK_PARITY:
                    attrs["engine_mode"] = Backtest.RISK_PARITY
                    ss = {**pd_cfg, **caller_ss}
                    ss.setdefault("sizing", "construct_risk_parity")
                    # P11 D1 — thread the equity cap + real sleeve group labels so
                    # the gate-facing backtest classifies sleeves like the live cycle
                    # (inverse_vol_weights otherwise passes empty groups, silently
                    # no-op'ing the cap). Off unless rp_max_equity_pct > 0.
                    mep = float(getattr(strategy, "rp_max_equity_pct", 0) or 0)
                    if mep > 0:
                        from apps.portfolios.models import UniverseMembership
                        ss.setdefault("max_equity_pct", mep)
                        ss.setdefault("sleeve_groups", dict(
                            UniverseMembership.objects
                            .filter(universe=strategy.universe, effective_to__isnull=True)
                            .values_list("ticker", "sector")
                        ))
                    attrs["search_space"] = ss
                elif strategy.kind == PortfolioStrategy.KIND_TREND:
                    from apps.portfolios.construction import trend_config
                    attrs["engine_mode"] = Backtest.TREND
                    attrs["search_space"] = {**pd_cfg, **trend_config(strategy), **caller_ss}
                elif strategy.kind == PortfolioStrategy.KIND_SECTOR_MOMENTUM:
                    from apps.portfolios.construction import sector_momentum_config
                    attrs["engine_mode"] = Backtest.SECTOR_MOMENTUM
                    attrs["search_space"] = {
                        **pd_cfg, **sector_momentum_config(strategy), **caller_ss
                    }
                elif strategy.kind == PortfolioStrategy.KIND_XSEC_LONG_SHORT:
                    # P11 F (R5) scaffolding — validation backtests run on the same
                    # deterministic path; live-arming is gate-blocked until F1's
                    # survivorship-clean single-name data lands.
                    from apps.portfolios.construction import xsec_long_short_config
                    attrs["engine_mode"] = Backtest.XSEC_LONG_SHORT
                    attrs["search_space"] = {
                        **pd_cfg, **xsec_long_short_config(strategy), **caller_ss
                    }
        # P10 §B4: the deterministic walk-forward replays ONE fixed config (no IS
        # candidate search), so a stored n_candidates > 1 would lie — and would
        # incorrectly arm the deflation KPI. Force the field to match reality.
        if attrs.get("engine_mode") in Backtest.DETERMINISTIC_ENGINE_MODES:
            attrs["n_candidates"] = 1
        is_window = attrs.get("is_window_days", 252)
        oos_window = attrs.get("oos_window_days", 63)
        step = attrs.get("step_days", 63)
        # Overlapping OOS folds double-count sessions and the stitched-curve
        # builder cannot represent them (it restarts the chain at every fold
        # flip, flat-lining the overlap and losing real up-days).
        if step < oos_window:
            raise serializers.ValidationError(
                f"step_days ({step}) must be >= oos_window_days ({oos_window}) — "
                "a smaller step produces overlapping OOS folds"
            )
        master_days = (attrs["end_date"] - attrs["start_date"]).days
        if master_days < is_window + oos_window:
            raise serializers.ValidationError(
                "master window too short for one fold (need >= is_window + oos_window days)"
            )
        version = attrs.get("graph_version")
        if version is not None:
            from apps.graphs.submission import apply_graph_version, check_submittable

            user = getattr(self.context.get("request"), "user", None)
            check_submittable(version, user)
            apply_graph_version(attrs, version, user=user)
            # Keep the denormalized display label in sync alongside the FK.
            attrs["agent_graph_version"] = version.display_label
        return attrs
