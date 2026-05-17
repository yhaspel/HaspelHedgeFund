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
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Macro {self.as_of_date} {self.growth_quadrant}/{self.inflation_regime}"


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
