from decimal import Decimal

from django.conf import settings
from django.db import models


class DailyBar(models.Model):
    ticker = models.CharField(max_length=16, db_index=True)
    date = models.DateField(db_index=True)
    open = models.DecimalField(max_digits=18, decimal_places=6)
    high = models.DecimalField(max_digits=18, decimal_places=6)
    low = models.DecimalField(max_digits=18, decimal_places=6)
    close = models.DecimalField(max_digits=18, decimal_places=6)
    adjusted_close = models.DecimalField(max_digits=18, decimal_places=6)
    volume = models.BigIntegerField()
    source = models.CharField(max_length=32)
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("ticker", "date", "source")]
        indexes = [models.Index(fields=["ticker", "date"])]

    def __str__(self) -> str:
        return f"{self.ticker} {self.date}"


class Fundamental(models.Model):
    ticker = models.CharField(max_length=16, db_index=True)
    as_of_date = models.DateField(db_index=True)  # publication / effective date
    period_end = models.DateField()  # quarter the metric describes
    metric = models.CharField(max_length=64)
    value = models.DecimalField(max_digits=24, decimal_places=6)
    source = models.CharField(max_length=32)
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("ticker", "period_end", "metric", "source")]
        indexes = [models.Index(fields=["ticker", "as_of_date"])]

    def __str__(self) -> str:
        return f"{self.ticker} {self.metric} {self.period_end}"


class FilingRecord(models.Model):
    ticker = models.CharField(max_length=16, db_index=True)
    form_type = models.CharField(max_length=16)
    filed_at = models.DateField(db_index=True)
    period_end = models.DateField()
    accession = models.CharField(max_length=32, unique=True)
    url = models.URLField(max_length=500)
    text_excerpt = models.TextField()
    full_text_path = models.CharField(max_length=512, blank=True, default="")
    section_index = models.JSONField(default=dict, blank=True)
    fetched_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.ticker} {self.form_type} {self.filed_at}"


class MacroSeries(models.Model):
    """FRED time series observation, vintage-aware.

    `vintage_date` is the date the value was published (ALFRED). For backtest
    correctness we always query: latest vintage_date <= as_of_date.
    """
    series_id = models.CharField(max_length=32, db_index=True)
    date = models.DateField(db_index=True)
    vintage_date = models.DateField(db_index=True)
    value = models.DecimalField(max_digits=24, decimal_places=6, null=True, blank=True)
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("series_id", "date", "vintage_date")]
        indexes = [models.Index(fields=["series_id", "date", "vintage_date"])]

    def __str__(self) -> str:
        return f"{self.series_id} {self.date} v{self.vintage_date}"


class MacroSnapshot(models.Model):
    as_of_date = models.DateField(unique=True)
    growth_quadrant = models.CharField(max_length=16)
    inflation_regime = models.CharField(max_length=16)
    yield_curve_state = models.CharField(max_length=16)
    policy_stance = models.CharField(max_length=16)
    narrative = models.TextField()
    sector_implications = models.JSONField(default=dict, blank=True)
    series_used = models.JSONField(default=dict, blank=True)
    # P2m: deterministic Markov regime context computed from the always-modelled
    # universe. Optional — populated when prewarm has fresh snapshots; never
    # required by the macro agent.
    markov_consensus = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Macro {self.as_of_date} {self.growth_quadrant}/{self.inflation_regime}"


class RegimeModel(models.Model):
    """A fitted Markov regime model as of a point in time (P2m).

    One row per (ticker, as_of_date, model_type, config_hash). Always
    walk-forward: only daily bars whose `date < as_of_date` contribute to
    the fit, and `training_end_date` records the latest bar used.
    """

    LABELLED_MARKOV = "labelled_markov"
    GAUSSIAN_HMM = "gaussian_hmm"
    MODEL_TYPE_CHOICES = [
        (LABELLED_MARKOV, "Labelled Markov chain"),
        (GAUSSIAN_HMM, "Gaussian HMM"),
    ]

    ticker = models.CharField(max_length=16, db_index=True)
    as_of_date = models.DateField(db_index=True)
    model_type = models.CharField(
        max_length=24, choices=MODEL_TYPE_CHOICES, default=LABELLED_MARKOV
    )
    config_hash = models.CharField(max_length=64, db_index=True)

    return_window_days = models.SmallIntegerField(default=20)
    bull_threshold_return = models.DecimalField(
        max_digits=6, decimal_places=4, default=Decimal("0.0500")
    )
    bear_threshold_return = models.DecimalField(
        max_digits=6, decimal_places=4, default=Decimal("-0.0500")
    )
    price_field = models.CharField(max_length=24, default="adjusted_close")

    # Fitted parameters. transition_matrix is a 3x3 row-stochastic list-of-lists
    # ordered as [bear, sideways, bull]. state_labels echoes that ordering for
    # the HMM where we map structure→label by mean ordering.
    transition_matrix = models.JSONField()
    state_means = models.JSONField(null=True, blank=True)
    state_stds = models.JSONField(null=True, blank=True)
    state_labels = models.JSONField()
    stationary_distribution = models.JSONField()

    fit_observations = models.IntegerField()
    observations_available = models.IntegerField()
    fit_lookback_observations = models.IntegerField(default=2520)
    training_start_date = models.DateField()
    training_end_date = models.DateField()
    log_likelihood = models.FloatField(null=True, blank=True)
    fit_metadata = models.JSONField(default=dict, blank=True)
    fitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [
            ("ticker", "as_of_date", "model_type", "config_hash")
        ]
        indexes = [models.Index(fields=["ticker", "as_of_date"])]

    def __str__(self) -> str:
        return f"RegimeModel {self.ticker}@{self.as_of_date} {self.model_type}"


class RegimeSnapshot(models.Model):
    """Consumable summary derived from a RegimeModel (P2m).

    One row per (ticker, as_of_date, model_type, config_hash). Downstream
    code (strategies, dashboards, risk manager) reads this rather than the
    raw RegimeModel.
    """

    ticker = models.CharField(max_length=16, db_index=True)
    as_of_date = models.DateField(db_index=True)
    model_type = models.CharField(
        max_length=24,
        choices=RegimeModel.MODEL_TYPE_CHOICES,
        default=RegimeModel.LABELLED_MARKOV,
    )
    config_hash = models.CharField(max_length=64, db_index=True)
    source_model = models.ForeignKey(
        RegimeModel, related_name="snapshots", on_delete=models.CASCADE
    )

    last_price_date = models.DateField()
    current_state = models.CharField(max_length=12)  # bull | sideways | bear
    current_return = models.FloatField(null=True, blank=True)
    current_state_persistence = models.FloatField()
    bull_persistence = models.FloatField()
    sideways_persistence = models.FloatField()
    bear_persistence = models.FloatField()

    bull_prob_1d = models.FloatField()
    sideways_prob_1d = models.FloatField()
    bear_prob_1d = models.FloatField()
    bull_prob_5d = models.FloatField()
    sideways_prob_5d = models.FloatField()
    bear_prob_5d = models.FloatField()
    bull_minus_bear_1d = models.FloatField()

    prior_snapshot = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="successors",
    )
    prior_current_state = models.CharField(max_length=12, blank=True, default="")
    current_state_persistence_delta = models.FloatField(null=True, blank=True)
    bull_persistence_delta = models.FloatField(null=True, blank=True)
    bear_persistence_delta = models.FloatField(null=True, blank=True)
    state_changed_from_prior = models.BooleanField(default=False)
    stale = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [
            ("ticker", "as_of_date", "model_type", "config_hash")
        ]
        indexes = [
            models.Index(fields=["as_of_date", "model_type"]),
            models.Index(fields=["ticker", "as_of_date"]),
        ]

    def __str__(self) -> str:
        return f"RegimeSnap {self.ticker}@{self.as_of_date}={self.current_state}"


class NewsItem(models.Model):
    ticker = models.CharField(max_length=16, db_index=True)
    published_at = models.DateTimeField(db_index=True)
    headline = models.CharField(max_length=512)
    source = models.CharField(max_length=64)
    provider = models.CharField(max_length=32)  # "tiingo" | "fmp"
    url = models.URLField(max_length=1000)
    summary = models.TextField(blank=True, default="")
    raw_text = models.TextField(blank=True, default="")
    materiality_score = models.FloatField(null=True, blank=True)
    materiality_tag = models.CharField(max_length=32, blank=True, default="")
    dedup_key = models.CharField(max_length=64, db_index=True, blank=True, default="")
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("provider", "url")]
        indexes = [models.Index(fields=["ticker", "published_at"])]

    def __str__(self) -> str:
        return f"{self.ticker} {self.published_at:%Y-%m-%d} {self.headline[:60]}"


class CompanyProfile(models.Model):
    """Slow-changing public reference data for a ticker (P3 prereq 2 / WS-2).

    Name / exchange / sector rarely move and are not user-specific — store
    once, share across users. Quote-derived values (market cap, P/E, EPS)
    move daily and are intentionally NOT persisted here; they live in
    short-TTL Redis instead so the popover stays fresh without DB churn.

    This row is *today* data — it must never be consulted by any backtest
    or point-in-time agent path. The `TickerProfileView` is the only
    legitimate consumer.
    """
    ticker = models.CharField(max_length=16, unique=True, db_index=True)
    name = models.CharField(max_length=256, blank=True, default="")
    exchange = models.CharField(max_length=32, blank=True, default="")
    sector = models.CharField(max_length=64, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.ticker} ({self.name or 'unresolved'})"


class CorporateAction(models.Model):
    """Provider-backed corporate actions (splits, dividends, symbol changes).

    Backtests consult these instead of inferring from bar series. `as_of_date`
    is the ex-date / effective date — the date the action takes effect from
    the holder's perspective. `source` records which provider supplied the
    row so we can audit/disambiguate when two providers disagree.
    """

    SPLIT = "split"
    CASH_DIVIDEND = "cash_dividend"
    STOCK_DIVIDEND = "stock_dividend"
    SYMBOL_CHANGE = "symbol_change"
    DELISTING = "delisting"
    MERGER_CASH = "merger_cash"
    KIND_CHOICES = [
        (SPLIT, "Split"),
        (CASH_DIVIDEND, "Cash dividend"),
        (STOCK_DIVIDEND, "Stock dividend"),
        (SYMBOL_CHANGE, "Symbol change"),
        (DELISTING, "Delisting"),
        (MERGER_CASH, "Cash merger"),
    ]

    ticker = models.CharField(max_length=16, db_index=True)
    as_of_date = models.DateField(db_index=True)  # ex-date
    kind = models.CharField(max_length=24, choices=KIND_CHOICES)
    # For SPLIT: ratio (e.g. 2.0 for 2:1). For *_DIVIDEND: per-share amount.
    # For SYMBOL_CHANGE: new symbol in `new_symbol`. For MERGER_CASH: per-share cash.
    ratio = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    amount = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    new_symbol = models.CharField(max_length=16, blank=True, default="")
    source = models.CharField(max_length=32)  # "fmp" | "tiingo" | "manual"

    class Meta:
        indexes = [models.Index(fields=["ticker", "as_of_date", "kind"])]
        unique_together = [("ticker", "as_of_date", "kind", "source")]

    def __str__(self) -> str:
        return f"{self.ticker} {self.kind} {self.as_of_date}"


class MarketNewsItem(models.Model):
    """One market-news story from one provider (P3-prereq-4).

    Parallel to ``NewsItem`` but market-wide, not ticker-scoped. This is *today*
    data — it must never be consulted by any backtest or point-in-time agent
    path. The market-news view layer + service is the only legitimate consumer.

    Sentiment fields are populated by the frugal market-news sentiment
    classifier (``apps.data.market_news_sentiment``) and keyed to the
    ``sentiment_model`` that produced them so a model switch re-scores.
    """

    SENTIMENT_BULLISH = "bullish"
    SENTIMENT_BEARISH = "bearish"
    SENTIMENT_NEUTRAL = "neutral"
    SENTIMENT_CHOICES = [
        (SENTIMENT_BULLISH, "Bullish"),
        (SENTIMENT_BEARISH, "Bearish"),
        (SENTIMENT_NEUTRAL, "Neutral"),
    ]

    provider = models.CharField(max_length=16)  # "fmp" | "tiingo"
    headline = models.CharField(max_length=512)
    summary = models.TextField(blank=True, default="")
    url = models.URLField(max_length=1000)
    image_url = models.URLField(max_length=1000, blank=True, default="")
    source = models.CharField(max_length=128, blank=True, default="")
    published_at = models.DateTimeField(db_index=True)
    symbols = models.JSONField(default=list, blank=True)
    tags = models.JSONField(default=list, blank=True)
    dedup_key = models.CharField(max_length=64, db_index=True, blank=True, default="")
    # Sentiment (populated by market_news_sentiment; blank until classified).
    sentiment = models.CharField(
        max_length=8, choices=SENTIMENT_CHOICES, blank=True, default=""
    )
    sentiment_score = models.FloatField(null=True, blank=True)
    sentiment_rationale = models.CharField(max_length=240, blank=True, default="")
    sentiment_model = models.CharField(max_length=128, blank=True, default="")
    sentiment_at = models.DateTimeField(null=True, blank=True)
    fetched_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        unique_together = [("provider", "url")]
        indexes = [models.Index(fields=["published_at", "dedup_key"])]
        ordering = ["-published_at"]

    def __str__(self) -> str:
        return f"{self.provider}:{self.published_at:%Y-%m-%d} {self.headline[:60]}"


class UserNewsPreferences(models.Model):
    """Per-user News settings (P3-prereq-4).

    Mirrors ``apps.portfolios.models.PortfolioPreferences`` — feature-local,
    one row per user, auto-created on first read.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        related_name="news_prefs",
        on_delete=models.CASCADE,
    )
    sentiment_enabled = models.BooleanField(default=True)
    sentiment_model = models.CharField(
        max_length=128, default="openrouter:qwen/qwen3.6-27b"
    )
    chyron_enabled = models.BooleanField(default=True)
    chyron_item_count = models.PositiveSmallIntegerField(default=8)  # clamp 5–10
    feed_item_count = models.PositiveSmallIntegerField(default=20)  # clamp 10–20
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"news_prefs u={self.user_id}"
