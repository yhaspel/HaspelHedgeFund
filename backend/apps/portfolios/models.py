from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models


class Universe(models.Model):
    name = models.SlugField(max_length=64, unique=True)
    description = models.TextField(blank=True, default="")
    source = models.CharField(max_length=32, default="manual")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class UniverseMembership(models.Model):
    universe = models.ForeignKey(
        Universe, related_name="memberships", on_delete=models.CASCADE
    )
    ticker = models.CharField(max_length=16, db_index=True)
    sector = models.CharField(max_length=64, blank=True, default="")
    effective_from = models.DateField(db_index=True)
    effective_to = models.DateField(null=True, blank=True, db_index=True)

    class Meta:
        unique_together = [("universe", "ticker", "effective_from")]
        indexes = [models.Index(fields=["universe", "ticker"])]

    def __str__(self) -> str:
        return f"{self.universe_id}:{self.ticker}"


class Portfolio(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="portfolios", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=64)
    cash_balance = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("100000"))
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class Position(models.Model):
    portfolio = models.ForeignKey(
        Portfolio, related_name="positions", on_delete=models.CASCADE
    )
    ticker = models.CharField(max_length=16, db_index=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=6)  # negative = short
    avg_cost = models.DecimalField(max_digits=12, decimal_places=4)
    sector = models.CharField(max_length=64, blank=True, default="")
    opened_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("portfolio", "ticker")]

    def __str__(self) -> str:
        return f"{self.ticker} qty={self.quantity}"

    @property
    def is_short(self) -> bool:
        return self.quantity < 0


class PortfolioStrategy(models.Model):
    KIND_LONG_ONLY = "long_only"
    KIND_SHORT_ONLY = "short_only"
    KIND_LONG_SHORT = "long_short"
    KIND_MARKET_NEUTRAL = "market_neutral"
    KIND_CONCENTRATED_LONG = "concentrated_long"
    KIND_SECTOR_ROTATION = "sector_rotation"
    KIND_GLOBAL_MACRO = "global_macro"
    KIND_RISK_PARITY = "risk_parity"
    KIND_PAIRS = "pairs"
    KIND_CHOICES = [
        (KIND_LONG_ONLY, "Long-only"),
        (KIND_SHORT_ONLY, "Short-only"),
        (KIND_LONG_SHORT, "Long/Short"),
        (KIND_MARKET_NEUTRAL, "Market-neutral"),
        (KIND_CONCENTRATED_LONG, "Concentrated long-only"),
        (KIND_SECTOR_ROTATION, "Sector / thematic ETF rotation"),
        (KIND_GLOBAL_MACRO, "Global macro (ETF expression)"),
        (KIND_RISK_PARITY, "Risk-parity / multi-asset lite"),
        (KIND_PAIRS, "Pairs trading (cointegration)"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="strategies", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=KIND_LONG_SHORT)
    universe = models.ForeignKey(Universe, on_delete=models.PROTECT)
    portfolio = models.ForeignKey(Portfolio, on_delete=models.PROTECT)

    target_gross_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("1.50"))
    target_net_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.50"))
    max_position_pct = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.03"))
    max_sector_pct = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.25"))
    min_position_pct = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.005"))
    top_k_longs = models.IntegerField(default=10)
    top_k_shorts = models.IntegerField(default=5)

    personas = models.JSONField(default=list, blank=True)
    model_preset = models.CharField(max_length=16, default="hybrid")
    cost_ceiling_per_cycle_usd = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal("5.00")
    )

    min_trade_notional_usd = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("250")
    )
    max_turnover_pct = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.30"))

    # Screener weights (sliders). Higher = more weight in ranking.
    screener_weights = models.JSONField(default=dict, blank=True)

    # Market-neutral (kind=market_neutral) parameters.
    benchmark_ticker = models.CharField(max_length=16, default="SPY")
    beta_window_days = models.SmallIntegerField(default=252)
    neutrality_tolerance_dollar_pct = models.DecimalField(
        max_digits=5, decimal_places=4, default=Decimal("0.02")
    )
    neutrality_tolerance_beta = models.DecimalField(
        max_digits=5, decimal_places=4, default=Decimal("0.05")
    )
    drop_on_unreliable_beta = models.BooleanField(default=False)

    # Concentrated long-only (kind=concentrated_long) parameters.
    # P02g review: defaults aligned with the plan and UI (min_positions=5,
    # min_aggregate_confidence=0.65). The earlier weaker defaults
    # (min_positions=3, 0.55) let API-only callers create concentrated
    # strategies that didn't actually concentrate.
    max_positions = models.SmallIntegerField(default=15)
    min_positions = models.SmallIntegerField(default=5)
    min_aggregate_confidence = models.DecimalField(
        max_digits=4, decimal_places=3, default=Decimal("0.650")
    )

    # Sector rotation (kind=sector_rotation) parameters.
    max_etfs_held = models.SmallIntegerField(default=6)
    per_etf_max_pct = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.30"))
    per_etf_min_pct = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.05"))
    # Council-v2 switch for sector_rotation: when True, screener picks survive
    # unless a persona votes bearish at >= bearish_veto_threshold OR risk vetoes.
    # See development-plans/phase-02h-sector-rotation.md "Plan refinement 2".
    # New strategies opt in; pre-refinement rows are backfilled to False so the
    # behaviour change is explicit.
    use_sector_council_v2 = models.BooleanField(default=True)
    bearish_veto_threshold = models.DecimalField(
        max_digits=4, decimal_places=3, default=Decimal("0.700")
    )

    # Global macro (kind=global_macro) parameters.
    asset_class_caps = models.JSONField(default=dict, blank=True)
    prefer_inverse_etf_over_short = models.BooleanField(default=True)
    max_inverse_etf_hold_days = models.SmallIntegerField(default=14)

    # Risk-parity (kind=risk_parity) parameters.
    vol_window_days = models.SmallIntegerField(default=60)
    rebalance_band_pct = models.DecimalField(
        max_digits=5, decimal_places=4, default=Decimal("0.05")
    )
    enable_council_veto = models.BooleanField(default=False)

    # Pairs trading (kind=pairs) parameters.
    pair_entry_z = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("2.0"))
    pair_exit_z = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("0.5"))
    pair_stop_z = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("4.0"))
    pair_max_held = models.SmallIntegerField(default=8)
    pair_notional_pct = models.DecimalField(
        max_digits=5, decimal_places=4, default=Decimal("0.05")
    )
    pair_cointegration_p_max = models.DecimalField(
        max_digits=4, decimal_places=3, default=Decimal("0.05")
    )
    pair_lookback_days = models.SmallIntegerField(default=252)
    pair_correlation_min = models.DecimalField(
        max_digits=4, decimal_places=3, default=Decimal("0.700")
    )
    enable_pair_council = models.BooleanField(default=False)
    pair_council_min_confidence = models.DecimalField(
        max_digits=4, decimal_places=3, default=Decimal("0.500")
    )

    # P2m: optional Markov regime exposure scaler (off by default). When on,
    # the long-short cycle multiplies target_net_pct (or target_gross_pct) by
    # a clipped function of (bull_prob_1d - bear_prob_1d) for the configured
    # ticker. See development-plans/phase-02m-markov-regime-classifier.md.
    REGIME_SCALER_OFF = "off"
    REGIME_SCALER_NET = "scale_net"
    REGIME_SCALER_GROSS = "scale_gross"
    REGIME_SCALER_CHOICES = [
        (REGIME_SCALER_OFF, "Off"),
        (REGIME_SCALER_NET, "Scale net"),
        (REGIME_SCALER_GROSS, "Scale gross"),
    ]
    regime_exposure_scaler = models.CharField(
        max_length=16,
        choices=REGIME_SCALER_CHOICES,
        default=REGIME_SCALER_OFF,
    )
    regime_scaler_floor = models.DecimalField(
        max_digits=5, decimal_places=4, default=Decimal("0.2000")
    )
    regime_scaler_ceiling = models.DecimalField(
        max_digits=5, decimal_places=4, default=Decimal("1.0000")
    )
    regime_scaler_ticker = models.CharField(max_length=16, default="SPY")
    regime_scaler_allow_negative_net = models.BooleanField(default=False)
    enable_markov_regime_gate = models.BooleanField(default=False)
    markov_bear_prob_5d_threshold = models.DecimalField(
        max_digits=4, decimal_places=3, default=Decimal("0.850")
    )

    # P2l: gate council fan-out behind explicit user approval.
    # default=True preserves existing behavior; set False to stop the cycle at
    # `awaiting_review` after the cheap screener pass.
    auto_run_council = models.BooleanField(
        default=True,
        help_text=(
            "When true, dispatch council tasks immediately after screening. "
            "When false, stop at awaiting_review until the user approves the "
            "screened candidates."
        ),
    )

    is_active = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.universe.name})"


class SectorETF(models.Model):
    """Registry of investable sector / theme ETFs (P2h)."""
    ticker = models.CharField(max_length=16, unique=True)
    sector = models.CharField(max_length=64)
    theme = models.CharField(max_length=64, blank=True, default="")
    issuer = models.CharField(max_length=32, default="SPDR")
    aum_usd = models.BigIntegerField(default=0)
    avg_daily_volume_usd = models.BigIntegerField(default=0)
    expense_ratio_bps = models.SmallIntegerField(default=10)
    is_active = models.BooleanField(default=True)
    # 6-dim regime affinity: early_cycle, mid_cycle, late_cycle, recession,
    # rising_rates, sticky_inflation. Each in [-1, 1].
    regime_affinities = models.JSONField(default=dict, blank=True)
    description = models.TextField(blank=True, default="")

    def __str__(self) -> str:
        return f"{self.ticker} ({self.sector})"


class ETFHoldingSnapshot(models.Model):
    """P02h review: per-ETF constituent holdings snapshot.

    One row per (etf_ticker, constituent_ticker, as_of_date, source).
    Backed by FMP/Tiingo holdings endpoints or a manual import; the
    cycle reads the most recent snapshot for each ETF and computes
    pairwise overlap from holdings rather than hardcoded pairs.

    weight: constituent's weight in the ETF, in [0, 1].
    """

    etf_ticker = models.CharField(max_length=16, db_index=True)
    constituent_ticker = models.CharField(max_length=16, db_index=True)
    as_of_date = models.DateField(db_index=True)
    weight = models.DecimalField(max_digits=7, decimal_places=6)
    source = models.CharField(max_length=32, default="manual")
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [
            ("etf_ticker", "constituent_ticker", "as_of_date", "source"),
        ]
        indexes = [models.Index(fields=["etf_ticker", "as_of_date"])]

    def __str__(self) -> str:
        return f"{self.etf_ticker}@{self.as_of_date}:{self.constituent_ticker}"


class MacroETF(models.Model):
    """Registry of investable macro-expression ETFs (P2i).

    asset_class ∈ {equity, rates, inflation, commodity, fx_proxy, em}
    direction   ∈ {long, inverse}
    affinities are encoded on the same 6 axes as SectorETF for reuse with
    the existing macro_regime_vector helper.
    """
    ASSET_CLASS_CHOICES = [
        ("equity", "Equity"),
        ("rates", "Rates"),
        ("inflation", "Inflation"),
        ("commodity", "Commodity"),
        ("fx_proxy", "FX proxy"),
        ("em", "Emerging markets"),
    ]
    ticker = models.CharField(max_length=16, unique=True)
    asset_class = models.CharField(max_length=16, choices=ASSET_CLASS_CHOICES)
    direction = models.CharField(max_length=8, default="long")
    inverse_of = models.CharField(max_length=16, blank=True, default="")
    duration_years = models.FloatField(null=True, blank=True)
    issuer = models.CharField(max_length=32, default="")
    expense_ratio_bps = models.SmallIntegerField(default=10)
    is_active = models.BooleanField(default=True)
    regime_affinities = models.JSONField(default=dict, blank=True)
    description = models.TextField(blank=True, default="")
    tracking_note = models.TextField(blank=True, default="")

    def __str__(self) -> str:
        return f"{self.ticker} ({self.asset_class}/{self.direction})"


class MacroRegimeSnapshot(models.Model):
    """Frozen view of the macro regime that drove a given cycle (P2i)."""
    strategy = models.ForeignKey(
        PortfolioStrategy, related_name="macro_regime_snapshots", on_delete=models.CASCADE
    )
    as_of_date = models.DateField(db_index=True)
    growth_score = models.FloatField(default=0.0)
    inflation_score = models.FloatField(default=0.0)
    policy_stance = models.CharField(max_length=16, default="neutral")
    yield_curve_state = models.CharField(max_length=16, default="flat")
    risk_on_score = models.FloatField(default=0.0)
    regime_vector = models.JSONField(default=dict, blank=True)
    source_macro_snapshot_id = models.IntegerField(null=True, blank=True)
    raw = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("strategy", "as_of_date")]

    def __str__(self) -> str:
        return f"regime s={self.strategy_id} {self.as_of_date}"


class BorrowQuote(models.Model):
    ticker = models.CharField(max_length=16, db_index=True)
    as_of_date = models.DateField(db_index=True)
    available_shares = models.IntegerField(null=True, blank=True)
    fee_pct_annual = models.DecimalField(max_digits=6, decimal_places=4, default=Decimal("0.01"))
    is_locatable = models.BooleanField(default=True)
    source = models.CharField(max_length=16, default="stub")

    class Meta:
        unique_together = [("ticker", "as_of_date", "source")]

    def __str__(self) -> str:
        return f"{self.ticker}@{self.as_of_date}"


class ScreenerRanking(models.Model):
    strategy = models.ForeignKey(
        PortfolioStrategy, related_name="rankings", on_delete=models.CASCADE
    )
    as_of_date = models.DateField(db_index=True)
    long_candidates = models.JSONField(default=list)
    short_candidates = models.JSONField(default=list)
    universe_size_evaluated = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"rank s={self.strategy_id} {self.as_of_date}"


class PortfolioTarget(models.Model):
    QUEUED = "queued"
    SCREENING = "screening"
    AWAITING_REVIEW = "awaiting_review"
    RUNNING_COUNCIL = "running_council"
    CONSTRUCTING = "constructing"
    # Legacy "running" preserved for back-compat with rows written before P2l.
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (QUEUED, "Queued"),
        (SCREENING, "Screening"),
        (AWAITING_REVIEW, "Awaiting review"),
        (RUNNING_COUNCIL, "Running council"),
        (CONSTRUCTING, "Constructing"),
        (RUNNING, "Running"),
        (DONE, "Done"),
        (FAILED, "Failed"),
        (CANCELLED, "Cancelled"),
    ]
    # Non-terminal: still progressing. Terminal: done|failed|cancelled.
    ACTIVE_STATUSES = {QUEUED, SCREENING, AWAITING_REVIEW, RUNNING_COUNCIL, CONSTRUCTING, RUNNING}

    strategy = models.ForeignKey(
        PortfolioStrategy, related_name="targets", on_delete=models.CASCADE
    )
    as_of_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    target_weights = models.JSONField(default=dict)  # {ticker: signed_weight_pct}
    gross_pct = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0"))
    net_pct = models.DecimalField(max_digits=6, decimal_places=4, default=Decimal("0"))
    sector_exposure = models.JSONField(default=dict)
    realised_net_pct = models.DecimalField(max_digits=6, decimal_places=4, default=Decimal("0"))
    realised_portfolio_beta = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("0")
    )
    beta_diagnostics = models.JSONField(default=dict, blank=True)
    per_position_thesis = models.JSONField(default=dict, blank=True)
    # P02g review: cycle_outcome enum (string, kept as CharField for flexibility
    # but values are constrained to this list). Documented here so analytics /
    # UI copy can rely on a stable contract:
    #   - "target_created"       — new target weights emitted; orders may follow.
    #   - "held_existing_book"   — no new target (concentrated_long below
    #                              min_positions, or no actionable candidates).
    #                              An empty target row is still written for
    #                              audit history; orders are NOT generated.
    #   - "within_rebalance_band"— risk-parity skipped this cycle because no
    #                              sleeve drifted outside its band.
    #   - "blocked_insufficient_confidence" — every candidate cleared but the
    #                              aggregate confidence bar wasn't met.
    #   - "risk_off"             — risk manager vetoed the whole book.
    cycle_outcome = models.CharField(max_length=32, blank=True, default="")
    rejected_candidates = models.JSONField(default=list)
    decisions = models.JSONField(default=list)
    # Sector-rotation v2: per-ETF veto reasoning. Each item:
    # {ticker, decision: "buy"|"veto", reasons: [{persona, signal, confidence}], rm_veto: bool}
    sector_veto_log = models.JSONField(default=list, blank=True)
    screener_ranking = models.ForeignKey(
        ScreenerRanking, null=True, blank=True, on_delete=models.SET_NULL
    )
    total_cost_usd = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0"))
    error_message = models.TextField(blank=True, default="")
    celery_task_id = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["strategy", "-as_of_date"])]
        # P2l: partial unique constraint. Cancelled cycles are terminal
        # history and must not block a same-day rerun. Race-safety on the
        # active branch is preserved by the partial unique + update_or_create.
        constraints = [
            models.UniqueConstraint(
                fields=["strategy", "as_of_date"],
                condition=~models.Q(status="cancelled"),
                name="uniq_active_strategy_target_per_day",
            ),
        ]

    def __str__(self) -> str:
        return f"target s={self.strategy_id} {self.as_of_date} {self.status}"


class PortfolioTargetRun(models.Model):
    """P2l: links one strategy-cycle candidate to its transcript Run."""
    target = models.ForeignKey(
        PortfolioTarget,
        related_name="candidate_run_links",
        on_delete=models.CASCADE,
    )
    run = models.OneToOneField(
        "runs.Run",
        related_name="portfolio_target_run_link",
        on_delete=models.CASCADE,
    )

    # For normal strategies this is the ticker. For pair council it is
    # "LEG_A/LEG_B"; Run.tickers stores the actual legs.
    candidate_key = models.CharField(max_length=64)
    primary_ticker = models.CharField(max_length=16, blank=True, default="")
    side = models.CharField(max_length=12)  # long | short | sector | pair
    borrow_veto = models.BooleanField(default=False)
    screener_rank = models.PositiveIntegerField()
    screener_score = models.FloatField(null=True, blank=True)
    sector = models.CharField(max_length=64, blank=True, default="")
    candidate_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["target", "candidate_key", "side"],
                name="uniq_target_candidate_side",
            ),
            models.UniqueConstraint(
                fields=["target", "run"],
                name="uniq_target_candidate_run",
            ),
        ]
        indexes = [
            models.Index(
                fields=["target", "screener_rank"],
                name="ptr_target_rank_idx",
            ),
            models.Index(
                fields=["target", "candidate_key"],
                name="ptr_target_key_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"target={self.target_id} run={self.run_id} {self.candidate_key}({self.side})"


class BetaEstimate(models.Model):
    """Rolling-window beta cache vs a benchmark (default SPY)."""
    ticker = models.CharField(max_length=16, db_index=True)
    benchmark = models.CharField(max_length=16, default="SPY", db_index=True)
    as_of_date = models.DateField(db_index=True)
    window_days = models.SmallIntegerField(default=252)
    beta = models.DecimalField(max_digits=6, decimal_places=3)
    r_squared = models.DecimalField(max_digits=5, decimal_places=3)
    n_observations = models.SmallIntegerField()
    reliable = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("ticker", "benchmark", "as_of_date", "window_days")]
        indexes = [models.Index(fields=["ticker", "as_of_date"])]

    def __str__(self) -> str:
        return f"β {self.ticker}/{self.benchmark}@{self.as_of_date} = {self.beta}"


class VolEstimate(models.Model):
    """Rolling-window daily-return volatility cache (P2j)."""
    ticker = models.CharField(max_length=16, db_index=True)
    as_of_date = models.DateField(db_index=True)
    window_days = models.SmallIntegerField(default=60)
    daily_vol = models.DecimalField(max_digits=8, decimal_places=6)
    annualised_vol = models.DecimalField(max_digits=8, decimal_places=6)
    n_observations = models.SmallIntegerField()
    synthetic = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("ticker", "as_of_date", "window_days")]

    def __str__(self) -> str:
        return f"vol {self.ticker}@{self.as_of_date} σ={self.daily_vol}"


class PairZHistory(models.Model):
    """Per-day z-score / spread for an open pair. One row per Pair per cycle."""
    pair = models.ForeignKey("Pair", related_name="z_history", on_delete=models.CASCADE)
    as_of_date = models.DateField(db_index=True)
    z = models.FloatField()
    spread = models.FloatField()
    leg_a_close = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    leg_b_close = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("pair", "as_of_date")]
        indexes = [models.Index(fields=["pair", "as_of_date"])]

    def __str__(self) -> str:
        return f"z {self.pair_id}@{self.as_of_date} z={self.z:.2f}"


class Pair(models.Model):
    """A candidate / open / closed pairs-trading pair (P2k)."""
    STATUS_CHOICES = [
        ("candidate", "Candidate"),
        ("open", "Open"),
        ("closed", "Closed"),
    ]
    strategy = models.ForeignKey(
        PortfolioStrategy, related_name="pairs", on_delete=models.CASCADE
    )
    leg_a_ticker = models.CharField(max_length=16)
    leg_b_ticker = models.CharField(max_length=16)
    sector = models.CharField(max_length=64, blank=True, default="")
    cointegration_p_value = models.FloatField(default=1.0)
    correlation = models.FloatField(default=0.0)
    hedge_ratio = models.FloatField(default=1.0)
    spread_mean = models.FloatField(default=0.0)
    spread_std = models.FloatField(default=0.0)
    spread_window_days = models.SmallIntegerField(default=252)
    entry_date = models.DateField(null=True, blank=True)
    entry_z = models.FloatField(null=True, blank=True)
    exit_date = models.DateField(null=True, blank=True)
    exit_z = models.FloatField(null=True, blank=True)
    exit_reason = models.CharField(max_length=24, blank=True, default="")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="candidate")
    notional_per_leg_usd = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    council_action = models.CharField(max_length=12, blank=True, default="")
    council_confidence = models.FloatField(null=True, blank=True)
    council_thesis = models.TextField(blank=True, default="")
    council_votes = models.JSONField(default=list, blank=True)
    consecutive_coint_failures = models.SmallIntegerField(default=0)
    hedge_ratio_drift_pct = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["strategy", "status"])]

    def __str__(self) -> str:
        return f"{self.leg_a_ticker}/{self.leg_b_ticker} ({self.status})"


class RebalanceOrder(models.Model):
    SIDE_CHOICES = [
        ("buy", "Buy"),
        ("sell", "Sell"),
        ("short", "Short"),
        ("cover", "Cover"),
    ]
    REASON_CHOICES = [
        ("open", "Open"),
        ("resize_up", "Resize up"),
        ("resize_down", "Resize down"),
        ("close", "Close"),
        ("risk_reduce", "Risk reduce"),
    ]
    target = models.ForeignKey(PortfolioTarget, related_name="orders", on_delete=models.CASCADE)
    ticker = models.CharField(max_length=16, db_index=True)
    side = models.CharField(max_length=8, choices=SIDE_CHOICES)
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    limit_price = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    reason = models.CharField(max_length=16, choices=REASON_CHOICES)
    estimated_notional_usd = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0")
    )
    sequence = models.IntegerField(default=2)
    pair = models.ForeignKey(
        "Pair", null=True, blank=True, related_name="orders", on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence", "ticker"]

    def __str__(self) -> str:
        return f"{self.side} {self.ticker} qty={self.quantity}"
