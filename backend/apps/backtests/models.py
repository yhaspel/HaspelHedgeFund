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

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="backtests", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=200)
    universe = models.JSONField(default=list)
    start_date = models.DateField()
    end_date = models.DateField()
    starting_cash = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("100000"))
    commission_bps = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("5"))
    spread_bps = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("5"))
    agent_graph_version = models.CharField(max_length=64, default="council-v1")
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

    def __str__(self) -> str:
        return f"Backtest {self.pk} {self.name} ({self.status})"


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
