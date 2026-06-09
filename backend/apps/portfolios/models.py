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
    KIND_STRATEGY = "strategy"
    KIND_MANUAL = "manual"
    KIND_BROKER = "broker"
    KIND_CHOICES = [
        (KIND_STRATEGY, "Strategy"),
        (KIND_MANUAL, "Manual"),
        (KIND_BROKER, "Broker"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="portfolios", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=64)
    cash_balance = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("100000"))
    # P3: distinguishes the per-user Manual Book from autonomous strategy books.
    # A strategy.portfolio FK must never point at a kind="manual" row.
    kind = models.CharField(
        max_length=12,
        choices=KIND_CHOICES,
        default=KIND_STRATEGY,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(kind="manual"),
                name="uniq_manual_portfolio_per_user",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Position(models.Model):
    OPENED_VIA_MANUAL = "manual"
    OPENED_VIA_RUN = "run"
    OPENED_VIA_STRATEGY_CYCLE = "strategy_cycle"
    OPENED_VIA_CHOICES = [
        (OPENED_VIA_MANUAL, "Manual entry"),
        (OPENED_VIA_RUN, "From run decision"),
        (OPENED_VIA_STRATEGY_CYCLE, "Strategy cycle"),
    ]

    portfolio = models.ForeignKey(
        Portfolio, related_name="positions", on_delete=models.CASCADE
    )
    ticker = models.CharField(max_length=16, db_index=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=6)  # negative = short
    avg_cost = models.DecimalField(max_digits=12, decimal_places=4)
    sector = models.CharField(max_length=64, blank=True, default="")
    opened_at = models.DateTimeField(auto_now_add=True)
    # P3: provenance + cumulative realized P&L (manual book).
    opened_via = models.CharField(
        max_length=20, choices=OPENED_VIA_CHOICES, default=OPENED_VIA_MANUAL,
    )
    source_run = models.ForeignKey(
        "runs.Run", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="opened_positions",
    )
    source_decision = models.ForeignKey(
        "runs.Decision", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="opened_positions",
    )
    note = models.TextField(blank=True, default="")
    realized_pnl = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0"),
    )

    class Meta:
        unique_together = [("portfolio", "ticker")]

    def __str__(self) -> str:
        return f"{self.ticker} qty={self.quantity}"

    @property
    def is_short(self) -> bool:
        return self.quantity < 0


class PortfolioPreferences(models.Model):
    """P3: per-user Manual Book preferences (mark cadence + interval).

    The `mark_cadence` controls how `valuation.get_mark()` resolves prices:

    - ``daily``   — last daily close via ``FmpProvider.get_daily_bars()``.
                    Cache TTL 10 minutes. Stale > 4 calendar days.
    - ``delayed`` — intraday quote via ``FmpProvider.get_latest_quote()``
                    (premium FMP plan; ~15-min delayed by default,
                    near-real-time with the live entitlement). Cache TTL
                    matches ``interval_minutes`` so manual reloads inside
                    the window reuse the fetch.
    - ``manual``  — same data source as ``delayed``, but the frontend never
                    auto-polls. Cache is invalidated by
                    ``POST /api/portfolio/refresh-marks/``.

    ``interval_minutes`` only applies to ``delayed`` (auto-poll period)
    and ``manual`` (cache TTL); ``daily`` ignores it.
    """

    CADENCE_DAILY = "daily"
    CADENCE_DELAYED = "delayed"
    CADENCE_MANUAL = "manual"
    CADENCE_CHOICES = [
        (CADENCE_DAILY, "Daily"),
        (CADENCE_DELAYED, "Delayed (auto-refresh)"),
        (CADENCE_MANUAL, "Pull only (manual refresh)"),
    ]

    MIN_INTERVAL_MINUTES = 5
    MAX_INTERVAL_MINUTES = 1440
    DEFAULT_INTERVAL_MINUTES = 20

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        related_name="portfolio_preferences",
        on_delete=models.CASCADE,
    )
    mark_cadence = models.CharField(
        max_length=12, choices=CADENCE_CHOICES, default=CADENCE_DAILY,
    )
    interval_minutes = models.PositiveSmallIntegerField(
        default=DEFAULT_INTERVAL_MINUTES,
    )
    last_refreshed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"portfolio_prefs u={self.user_id} cadence={self.mark_cadence}"


class LedgerEntry(models.Model):
    """Append-only ledger of every cash/position mutation on the Manual Book.

    Every mutation is wrapped in ``transaction.atomic()`` with a matching
    LedgerEntry row so the book reconciles: initial cash + Σ cash_delta =
    current Portfolio.cash_balance.
    """

    KIND_DEPOSIT = "deposit"
    KIND_WITHDRAWAL = "withdrawal"
    KIND_OPEN = "position_open"
    KIND_INCREASE = "position_increase"
    KIND_REDUCE = "position_reduce"
    KIND_CLOSE = "position_close"
    KIND_EDIT = "edit_adjustment"
    # P3a-1: broker-fill ledger entries for kind="broker" portfolios.
    KIND_BROKER_FILL = "broker_fill"
    KIND_RECONCILE = "reconciliation_adjustment"
    # P4 WS-E: strategy-cycle enrollment legs (open/increase vs reduce vs close)
    # written into a kind="strategy" portfolio when a done cycle is enrolled.
    KIND_STRATEGY_ENROLL = "strategy_enroll"
    KIND_STRATEGY_ENROLL_REDUCE = "strategy_enroll_reduce"
    KIND_STRATEGY_ENROLL_CLOSE = "strategy_enroll_close"
    KIND_CHOICES = [
        (KIND_DEPOSIT, "Cash deposit"),
        (KIND_WITHDRAWAL, "Cash withdrawal"),
        (KIND_OPEN, "Position opened"),
        (KIND_INCREASE, "Position increased"),
        (KIND_REDUCE, "Position reduced"),
        (KIND_CLOSE, "Position closed"),
        (KIND_EDIT, "Manual edit adjustment"),
        (KIND_BROKER_FILL, "Broker fill"),
        (KIND_RECONCILE, "Reconciliation adjustment"),
        (KIND_STRATEGY_ENROLL, "Strategy enrollment (open/increase)"),
        (KIND_STRATEGY_ENROLL_REDUCE, "Strategy enrollment (reduce)"),
        (KIND_STRATEGY_ENROLL_CLOSE, "Strategy enrollment (close)"),
    ]

    portfolio = models.ForeignKey(
        Portfolio, related_name="ledger", on_delete=models.CASCADE,
    )
    kind = models.CharField(max_length=32, choices=KIND_CHOICES)
    ticker = models.CharField(max_length=16, blank=True, default="")
    quantity_delta = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal("0"),
    )
    price = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True,
    )
    cash_delta = models.DecimalField(max_digits=14, decimal_places=2)
    realized_pnl = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0"),
    )
    quantity_after = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True,
    )
    cash_balance_after = models.DecimalField(max_digits=14, decimal_places=2)
    position = models.ForeignKey(
        Position, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="ledger_entries",
    )
    source_run = models.ForeignKey(
        "runs.Run", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="ledger_entries",
    )
    source_decision = models.ForeignKey(
        "runs.Decision", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="ledger_entries",
    )
    # P3a-1: broker-side provenance. Both nullable so the prereq Manual Book
    # rows continue to work without changes.
    broker_order = models.ForeignKey(
        "brokers.BrokerOrder", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="ledger_entries",
    )
    broker_sync_event = models.ForeignKey(
        "brokers.BrokerSyncEvent", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="ledger_entries",
    )
    note = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="ledger_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["portfolio", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"ledger {self.kind} {self.ticker} {self.cash_delta}"


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

    # Deterministic, council-free kinds. Their target weights are fully sized
    # and capped inside their constructor (``construct_risk_parity`` /
    # the pairs constructor), so the autopilot→broker bridge must NOT re-apply
    # vol-targeting or the equity per-name cap to them — doing so would diverge
    # the live book from the validated backtest (ADR 0025 §2).
    DETERMINISTIC_KINDS = frozenset({KIND_RISK_PARITY, KIND_PAIRS})

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
    # P4 WS-E: when True, the cycle done-handler auto-materializes target_weights
    # into the strategy portfolio (skips the manual confirm modal). Off by
    # default so existing strategies are unchanged.
    auto_enroll_on_done = models.BooleanField(default=False)
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

    # P3-prereq-5 WS-G: opt this strategy's autonomous cycle Runs in to
    # investor-profile personalization. Default off preserves existing
    # strategy behaviour when the owner takes the questionnaire.
    apply_investor_profile = models.BooleanField(
        default=False,
        help_text=(
            "When true and the owner's profile master switch is on, this "
            "strategy's cycle Runs are personalized to the owner's investor "
            "profile (CIO, personas and Risk Manager narrative only)."
        ),
    )

    is_active = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # P7: bumped on every save (caps/weights edits). The §9 autopilot
    # validation gate compares a qualifying backtest's created_at against this
    # so editing a strategy's config re-locks the enable toggle until a fresh
    # passing walk-forward backtest exists (staleness rule).
    updated_at = models.DateTimeField(auto_now=True, null=True)

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
    # P7: a cycle whose RebalanceOrder plan was autonomously converted to paper
    # BrokerOrders and submitted by the autopilot bridge. Treated as TERMINAL
    # (like ``done``) and deliberately kept OUT of ``ACTIVE_STATUSES`` — that
    # set is the dispatcher's "still in flight" guard, so marking a finished
    # submitted cycle active would make it look perpetually running and block
    # the next weekly dispatch.
    AUTOPILOT_SUBMITTED = "autopilot_submitted"
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
        (AUTOPILOT_SUBMITTED, "Autopilot submitted"),
    ]
    # Non-terminal: still progressing. Terminal: done|failed|cancelled|
    # autopilot_submitted.
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
    # P3 addendum: per-cycle mark-to-market snapshot. Treats `target_weights`
    # as a hypothetical-hold book and computes per-ticker return since
    # `as_of_date` plus book-level marked gross/net/return — without
    # requiring broker fills. See ``apps/portfolios/cycle_mark.py``.
    # Shape: {snapshot_at, mark_as_of, since_as_of_pct, marked_gross_pct,
    #         marked_net_pct, per_ticker: {ticker: {weight_pct, as_of_price,
    #         mark_price, return_pct, contribution_pp, warnings}},
    #         warnings: [str]}.
    marked_snapshot = models.JSONField(default=dict, blank=True)
    # P3b council-alpha: the council-free deterministic book for this cycle
    # (same constructor, screener score in place of council confidence, no
    # veto). Captured at finalize time so the nightly leaderboard can mark it
    # with the same machinery and measure realised − baseline. See
    # ``apps/leaderboard/council_alpha.py``. ``baseline_marked_snapshot`` mirrors
    # ``marked_snapshot``'s shape; ``baseline_version`` stamps the baseline
    # definition so incompatible series aren't mixed (plan risk #7).
    baseline_weights = models.JSONField(default=dict, blank=True)
    baseline_marked_snapshot = models.JSONField(default=dict, blank=True)
    baseline_version = models.CharField(max_length=16, blank=True, default="")
    screener_ranking = models.ForeignKey(
        ScreenerRanking, null=True, blank=True, on_delete=models.SET_NULL
    )
    total_cost_usd = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0"))
    error_message = models.TextField(blank=True, default="")
    celery_task_id = models.CharField(max_length=64, blank=True, default="")
    # P4 WS-B: rerun provenance. When a failed/cancelled cycle is rerun, the
    # OLD row points here at the fresh row so the cycles list can render a
    # "↻ superseded" pill instead of duplicating both into the user's view.
    superseded_by = models.ForeignKey(
        "self", null=True, blank=True,
        related_name="supersedes", on_delete=models.SET_NULL,
    )
    # P4 WS-E: set when the user materializes this cycle's target_weights into
    # the strategy portfolio. NULL until enrolled — the cycle-detail card uses
    # this to switch between "Enter strategy" and "Enrolled — view positions".
    enrolled_at = models.DateTimeField(null=True, blank=True)
    # P4 WS-E: frozen snapshot of {ticker: {action, quantity_delta, notional,
    # mark_price}} at enrollment time, so the audit trail survives later closes.
    enrollment_diff = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["strategy", "-as_of_date"])]
        # P2l/P4 WS-B: partial unique constraint. Cancelled AND failed cycles
        # are terminal history and must not block a same-day rerun (a rerun
        # creates a fresh row and supersedes the old terminal one). Race-safety
        # on the active branch is preserved by the partial unique + the
        # non-superseded row resolution in tasks._resolve_cycle_target.
        constraints = [
            models.UniqueConstraint(
                fields=["strategy", "as_of_date"],
                condition=~models.Q(status__in=["cancelled", "failed"]),
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


# ---------------------------------------------------------------------------
# P7 — Autonomous fund: per-strategy autopilot + run history + fund roster.
# These mirror the proven ``ScheduledRun`` / ``ScheduledRunHistory`` shapes
# (apps/schedules/models.py). All additive — a strategy with no autopilot row
# behaves exactly as before P7.
# ---------------------------------------------------------------------------
class StrategyAutopilot(models.Model):
    """A per-strategy weekly scheduler + deterministic guardrail config.

    Mirrors ``ScheduledRun`` (cron + market gate + cost ceiling + on_breach)
    and adds the pod-style risk guardrails (vol target, drawdown breaker,
    daily caps, liquidity floor) and the state machine the dispatcher and
    guardrail sweep key on. ``is_enabled`` is the per-account kill switch and
    cannot be set true until the §9 validation gate passes (enforced in the API
    layer). Guardrail ``*_pct`` fields are WHOLE PERCENTS (10 = 10%) — a
    deliberate, separate convention from the strategy's fraction fields.
    """

    ON_BREACH_DEGRADE = "degrade"
    ON_BREACH_SKIP = "skip"
    ON_BREACH_NOTIFY = "notify_only"
    ON_BREACH_CHOICES = [
        (ON_BREACH_DEGRADE, "Degrade to a cheaper preset"),
        (ON_BREACH_SKIP, "Skip the cycle"),
        (ON_BREACH_NOTIFY, "Run anyway and notify"),
    ]

    SHORT_SINGLE_NAME = "single_name"
    SHORT_ETF_HEDGE = "etf_hedge"
    SHORT_CASH = "cash"
    SHORT_MODE_CHOICES = [
        (SHORT_SINGLE_NAME, "Single-name shorts"),
        (SHORT_ETF_HEDGE, "ETF hedge"),
        (SHORT_CASH, "Cash (no shorts)"),
    ]

    STATE_ACTIVE = "active"
    STATE_SOFT_CUT = "soft_cut"
    STATE_HALTED = "halted"
    STATE_CHOICES = [
        (STATE_ACTIVE, "Active"),
        (STATE_SOFT_CUT, "Soft cut (gross halved)"),
        (STATE_HALTED, "Halted"),
    ]

    strategy = models.OneToOneField(
        PortfolioStrategy, on_delete=models.CASCADE, related_name="autopilot",
    )
    is_enabled = models.BooleanField(default=False)
    cron_expression = models.CharField(max_length=64, default="30 16 * * 5")
    timezone = models.CharField(max_length=64, default="America/New_York")
    is_market_aware = models.BooleanField(default=True)
    broker_account = models.ForeignKey(
        "brokers.BrokerAccount", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="autopilots",
    )
    model_preset = models.CharField(max_length=32, default="frugal")
    cost_ceiling_usd = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
    )
    on_breach = models.CharField(
        max_length=16, choices=ON_BREACH_CHOICES, default=ON_BREACH_DEGRADE,
    )

    # Guardrails (research defaults). Whole-percent convention.
    target_vol_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("10"))
    dd_soft_cut_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("5"))
    dd_hard_halt_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("7.5"))
    max_orders_per_day = models.PositiveIntegerField(default=30)
    max_notional_per_day_usd = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("50000"),
    )
    liquidity_adv_cap_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("5"),
    )
    short_mode = models.CharField(
        max_length=16, choices=SHORT_MODE_CHOICES, default=SHORT_SINGLE_NAME,
    )
    # §6.3: on a hard halt, freeze (default) or auto-flatten to cash via the
    # same gated order path.
    flatten_on_halt = models.BooleanField(default=False)

    # State machine (deterministic; the dispatcher trusts these, not the caller).
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=STATE_ACTIVE)
    peak_equity_usd = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True,
    )
    notification_channel = models.ForeignKey(
        "notifications.NotificationChannel", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="autopilots",
    )

    last_run_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:  # pragma: no cover
        return f"autopilot s={self.strategy_id} {self.state} enabled={self.is_enabled}"

    def reschedule(self, after=None) -> None:
        """Recompute ``next_run_at`` (reuses the scheduler's cron helper). No-op
        (clears the next fire) when disabled or the cron is invalid."""
        from apps.schedules.triggers import compute_next, is_valid_cron

        if self.is_enabled and is_valid_cron(self.cron_expression):
            self.next_run_at = compute_next(self.cron_expression, self.timezone, after=after)
        else:
            self.next_run_at = None


class AutopilotRun(models.Model):
    """One row per autopilot fire — mirrors ``ScheduledRunHistory`` (idempotency
    keyed on ``(autopilot, fire_time_utc)`` so a beat restart can't
    double-dispatch). Records the cycle target, the broker orders it emitted,
    and the deterministic guardrail actions (vol scale, caps hit, dd state)."""

    PENDING = "pending"
    RUNNING = "running"
    SUBMITTED = "submitted"
    SKIPPED = "skipped"
    HALTED = "halted"
    FAILED = "failed"
    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (RUNNING, "Running"),
        (SUBMITTED, "Submitted"),
        (SKIPPED, "Skipped"),
        (HALTED, "Halted"),
        (FAILED, "Failed"),
    ]

    autopilot = models.ForeignKey(
        StrategyAutopilot, on_delete=models.CASCADE, related_name="runs",
    )
    fire_time_utc = models.DateTimeField()
    target = models.ForeignKey(
        PortfolioTarget, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="autopilot_runs",
    )
    broker_orders = models.ManyToManyField(
        "brokers.BrokerOrder", related_name="autopilot_runs", blank=True,
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=PENDING)
    submit_decision = models.JSONField(default=dict, blank=True)
    guardrail_actions = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["autopilot", "fire_time_utc"],
                name="uniq_autopilot_fire_time",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"autopilotrun(ap={self.autopilot_id} @ {self.fire_time_utc})"


class AutonomousFund(models.Model):
    """The fund-level layer over the (up to 3) isolated paper accounts.

    Persisted (not a settings roster) because the fund kill switch needs a
    mutable, persisted halt flag the ``/api/fund/halt/`` endpoint writes and the
    guardrail sweep reads. The roster is an M2M of strategies (one fund per
    strategy). See ADR 0018."""

    STATE_ACTIVE = "active"
    STATE_HALTED = "halted"
    STATE_CHOICES = [(STATE_ACTIVE, "Active"), (STATE_HALTED, "Halted")]

    name = models.CharField(max_length=80, default="Autonomous Fund")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="funds",
    )
    strategies = models.ManyToManyField(
        PortfolioStrategy, related_name="funds", blank=True,
    )
    # Whole-percent fund-wide drawdown halt (tighter than any single account).
    fund_dd_halt_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("6"),
    )
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=STATE_ACTIVE)
    peak_equity_usd = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:  # pragma: no cover
        return f"fund {self.name} ({self.state})"
