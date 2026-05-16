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


class FilingRecord(models.Model):
    ticker = models.CharField(max_length=16, db_index=True)
    form_type = models.CharField(max_length=16)
    filed_at = models.DateField(db_index=True)
    period_end = models.DateField()
    accession = models.CharField(max_length=32, unique=True)
    url = models.URLField(max_length=500)
    text_excerpt = models.TextField()
    fetched_at = models.DateTimeField(auto_now_add=True)
