from decimal import Decimal

from django.conf import settings
from django.db import models


class Backtest(models.Model):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ABORTED_BUDGET = "aborted_budget"
    ABORTED_PARTIAL = "aborted_partial"
    SYNTHETIC = "synthetic"
    STATUS_CHOICES = [
        (QUEUED, "Queued"), (RUNNING, "Running"), (DONE, "Done"),
        (FAILED, "Failed"), (CANCELLED, "Cancelled"),
        (ABORTED_BUDGET, "Aborted (budget)"),
        (ABORTED_PARTIAL, "Aborted (sparse data)"),
        (SYNTHETIC, "Synthetic"),
    ]
    ACTIVE_STATUSES = {QUEUED, RUNNING}

    # Engine mode: how positions are sized in the walk-forward.
    #   council         — LLM persona vote → PM aggregation (the default/legacy path)
    #   risk_parity     — deterministic inverse-vol sizing, no prime/council (free)
    #   trend           — deterministic time-series momentum (TSMOM), no council (free)
    #   sector_momentum — deterministic cross-sectional momentum, no council (free)
    COUNCIL = "council"
    RISK_PARITY = "risk_parity"
    TREND = "trend"
    SECTOR_MOMENTUM = "sector_momentum"
    ENGINE_MODE_CHOICES = [
        (COUNCIL, "Council (LLM vote)"),
        (RISK_PARITY, "Deterministic risk parity"),
        (TREND, "Deterministic trend (TSMOM)"),
        (SECTOR_MOMENTUM, "Deterministic sector momentum"),
    ]
    # Council-free engine modes routed through the deterministic walk-forward
    # (no prime, no LLM, $0). Their sizer is picked by ``search_space["sizing"]``.
    DETERMINISTIC_ENGINE_MODES = frozenset({RISK_PARITY, TREND, SECTOR_MOMENTUM})

    # P10 §B3 — data era. Backtests created before the dividend/total-return
    # data fix (PR #50, merged 2026-06-09 15:17 UTC) ran on price-only bars
    # known to understate returns/Sharpe; they are kept as history but are
    # excluded as §9 autopilot-gate evidence. The backfill lives in
    # migration 0012; new rows are total_return by construction.
    ERA_PRICE_ONLY = "price_only"
    ERA_TOTAL_RETURN = "total_return"
    DATA_ERA_CHOICES = [
        (ERA_PRICE_ONLY, "Price-only data (pre PR #50)"),
        (ERA_TOTAL_RETURN, "Total-return data"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="backtests", on_delete=models.CASCADE
    )
    # P7 §9: the strategy this backtest validates. The autopilot validation gate
    # reads a strategy's latest DONE backtest (created after the strategy's last
    # config edit) and requires positive OOS Sharpe + max DD within the
    # hard-halt limit before its autopilot can be enabled. Nullable — ad-hoc
    # backtests (the existing flow) carry no strategy link.
    strategy = models.ForeignKey(
        "portfolios.PortfolioStrategy",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="backtests",
    )
    name = models.CharField(max_length=200)
    universe = models.JSONField(default=list)
    start_date = models.DateField()
    end_date = models.DateField()
    starting_cash = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("100000"))
    commission_bps = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("5"))
    spread_bps = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("5"))
    engine_mode = models.CharField(
        max_length=20, choices=ENGINE_MODE_CHOICES, default=COUNCIL
    )
    agent_graph_version = models.CharField(max_length=64, default="council-v1")
    # P4c: the immutable agent-graph version this backtest executed on. NULL ⇒
    # the hardcoded council.py. The CharField above is kept as a denormalized
    # display label (e.g. "my-graph:v3") set alongside this FK; the FK is the
    # authoritative reference. PROTECT enforces version immutability.
    graph_version = models.ForeignKey(
        "graphs.AgentGraphVersion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    agent_versions = models.JSONField(default=dict, blank=True)
    model_overrides = models.JSONField(default=dict, blank=True)
    personas = models.JSONField(default=list, blank=True)
    rebalance_frequency = models.CharField(max_length=16, default="weekly")  # daily|weekly|monthly

    # Walk-forward config
    is_window_days = models.IntegerField(default=252)
    oos_window_days = models.IntegerField(default=63)
    step_days = models.IntegerField(default=63)
    search_space = models.JSONField(default=dict, blank=True)
    n_candidates = models.IntegerField(default=50)
    is_objective = models.CharField(max_length=16, default="sharpe")  # sharpe|sortino|calmar
    rng_seed = models.IntegerField(default=42)
    baseline = models.CharField(max_length=16, default="universe_ew")  # universe_ew|spy
    # "hold_existing" → action="hold" preserves current target (skip rebalance for that name);
    # "target_zero"   → action="hold" routes to liquidate (target_weight_pct=0).
    # See decision-semantics block in phase-02a-agent-council.md.
    hold_semantics = models.CharField(max_length=16, default="hold_existing")
    # Fraction of ticker-days the cache-prime phase actually populated (0–1).
    # If too low, the run transitions to ABORTED_PARTIAL rather than DONE.
    # Defaults to 0.0 (not yet measured) — was 1.0 historically, which falsely
    # signalled "fully primed" on every fresh backtest until prime_agent_cache
    # overwrote it at the end of the loop, misleading the UI and any operator
    # querying the API mid-run.
    prime_completeness = models.FloatField(default=0.0)
    # Floor on the prime success-rate before the run is allowed to advance
    # to the fold phase. 0.85 was the original production-quality bar; smoke
    # validation runs in 2026-05 surfaced sporadic upstream-provider failures
    # (Llama 3.3 70B 'tool_calls' empty-content, OpenInference 502s, JSON
    # parse races) that pushed ~22% of ticker-days to fail even after the
    # tool_calls adapter fix landed. 0.65 is the looser bar so a smoke run
    # with one bad upstream day still proceeds to fold construction; users
    # who care about signal density can raise it per-backtest.
    prime_min_completeness = models.FloatField(default=0.65)
    # CIO is a discretionary veto layer that runs after the PM aggregation.
    # Backtests defaulted to skipping it (engine.py hardcoded disable_cio=True)
    # to keep cache priming deterministic. The field exists so a per-backtest
    # opt-in (e.g. when validating a council pipeline that mirrors live runs)
    # is one POST flag away rather than a code change.
    disable_cio = models.BooleanField(default=True)

    # P10 §B3: which bar data the run was computed on (see DATA_ERA_CHOICES).
    data_era = models.CharField(
        max_length=16, choices=DATA_ERA_CHOICES, default=ERA_TOTAL_RETURN,
    )

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=QUEUED)
    progress_pct = models.IntegerField(default=0)
    progress_message = models.CharField(max_length=200, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    total_cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=Decimal("0"))
    # Hard kill-switch on cumulative LLM spend (USD). prime_agent_cache aborts
    # the run if total_cost_usd >= max_budget_usd. Default is intentionally
    # conservative; raise per-run from the UI if a larger sweep is justified.
    max_budget_usd = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal("4.00")
    )
    celery_task_id = models.CharField(max_length=64, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    # P10 §D4: soft archive (the graphs pattern — graphs/models.py). done /
    # failed rows are protected history and can never be deleted, so without
    # this the list is append-only forever. Archived rows are hidden from the
    # default list (?include_archived=1 to see them) and stay §9-gate-eligible
    # candidates only via their status/era — archiving is cosmetic, not
    # evidential.
    archived_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Backtest {self.pk} {self.name} ({self.status})"

    @property
    def deflation_meaningful(self) -> bool:
        """P10 §B4 — the OOS/IS "deflation" ratio guards against overfitting
        ONLY when an IS candidate search actually selected a config. For
        deterministic walk-forwards (one fixed config) and single-candidate
        runs it guards nothing, so the UI suppresses the KPI. The union is
        required: deterministic rows can store the model-default
        ``n_candidates=50`` (pre-P10 rows), and legacy engine modes (e.g.
        ``market_neutral``) are caught by ``n_candidates == 1``."""
        return (
            self.n_candidates > 1
            and self.engine_mode not in self.DETERMINISTIC_ENGINE_MODES
        )


class BacktestFold(models.Model):
    backtest = models.ForeignKey(Backtest, related_name="folds", on_delete=models.CASCADE)
    fold_index = models.IntegerField()
    is_start = models.DateField()
    is_end = models.DateField()
    oos_start = models.DateField()
    oos_end = models.DateField()
    winning_config = models.JSONField(default=dict, blank=True)
    is_sharpe = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0"))
    oos_sharpe = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0"))
    oos_return_pct = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0"))
    oos_max_drawdown_pct = models.DecimalField(
        max_digits=10, decimal_places=4, default=Decimal("0"),
    )
    candidates_scored = models.JSONField(default=list, blank=True)

    class Meta:
        unique_together = [("backtest", "fold_index")]
        ordering = ["fold_index"]

    def __str__(self) -> str:
        return f"Fold {self.fold_index} bt={self.backtest_id}"


class BacktestDay(models.Model):
    SEG_IS = "is"
    SEG_OOS = "oos"

    backtest = models.ForeignKey(Backtest, related_name="days", on_delete=models.CASCADE)
    fold = models.ForeignKey(
        BacktestFold, related_name="days", on_delete=models.CASCADE, null=True, blank=True
    )
    segment = models.CharField(max_length=8, default=SEG_OOS)
    date = models.DateField(db_index=True)
    cash = models.DecimalField(max_digits=18, decimal_places=2)
    positions = models.JSONField(default=list)
    portfolio_value = models.DecimalField(max_digits=18, decimal_places=2)
    decisions = models.JSONField(default=list, blank=True)  # list of PM decision dicts (intent)
    # Executed fills as produced by SimulatedPortfolio.execute — list of
    # {ticker, qty, price, commission, notional, cash_delta}. Authoritative
    # source for turnover; decisions[].notional reflects intent, not execution.
    fills = models.JSONField(default=list, blank=True)

    class Meta:
        indexes = [models.Index(fields=["backtest", "segment", "date"])]
        ordering = ["date"]

    def __str__(self) -> str:
        return f"BTDay bt={self.backtest_id} {self.date}"


class BacktestMetrics(models.Model):
    backtest = models.OneToOneField(
        Backtest, related_name="metrics", on_delete=models.CASCADE,
    )
    # Computed on stitched OOS curve.
    total_return_pct = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0"))
    annualized_return_pct = models.DecimalField(
        max_digits=10, decimal_places=4, default=Decimal("0"),
    )
    sharpe = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0"))
    sortino = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0"))
    max_drawdown_pct = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0"))
    hit_rate = models.DecimalField(max_digits=6, decimal_places=4, default=Decimal("0"))
    win_loss_ratio = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0"))
    turnover_pct = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0"))
    mean_is_sharpe = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0"))
    mean_oos_sharpe = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0"))
    sharpe_deflation = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0"))
    oos_sharpe_std = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0"))
    baseline_return_pct = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0"))
    # P10 §B1: the pre-fix (price-only, price-weighted) baseline value, preserved
    # once by the recompute_baselines management command so the rewrite is
    # reversible and §9-gate history stays reconstructible. NULL = never rewritten.
    baseline_return_pct_legacy = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True,
    )
    # P10 §B2: SPY-TR / QQQ-TR comparison block (see metrics.benchmark_stats):
    # {"SPY": {total_return_pct, annualized_return_pct, sharpe, max_drawdown_pct,
    #          beta, alpha_annual_pct, information_ratio}, "QQQ": {...}}.
    benchmarks = models.JSONField(default=dict, blank=True)
    per_agent_attribution = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:
        return f"Metrics bt={self.backtest_id}"


class LLMResponseCache(models.Model):
    cache_key = models.CharField(max_length=128, unique=True)
    agent_name = models.CharField(max_length=64, db_index=True)
    agent_version = models.CharField(max_length=32)
    response_json = models.JSONField(default=dict)
    tokens_in = models.IntegerField(default=0)
    tokens_out = models.IntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=Decimal("0"))
    hits = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    last_hit_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.agent_name}:{self.cache_key[:12]}"
