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
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="strategies", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=80)
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

    is_active = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.universe.name})"


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
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("running", "Running"),
        ("done", "Done"),
        ("failed", "Failed"),
    ]
    strategy = models.ForeignKey(
        PortfolioStrategy, related_name="targets", on_delete=models.CASCADE
    )
    as_of_date = models.DateField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="queued")
    target_weights = models.JSONField(default=dict)  # {ticker: signed_weight_pct}
    gross_pct = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0"))
    net_pct = models.DecimalField(max_digits=6, decimal_places=4, default=Decimal("0"))
    sector_exposure = models.JSONField(default=dict)
    rejected_candidates = models.JSONField(default=list)
    decisions = models.JSONField(default=list)
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

    def __str__(self) -> str:
        return f"target s={self.strategy_id} {self.as_of_date} {self.status}"


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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence", "ticker"]

    def __str__(self) -> str:
        return f"{self.side} {self.ticker} qty={self.quantity}"
