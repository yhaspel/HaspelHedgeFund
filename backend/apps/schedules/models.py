"""Scheduled runs + per-fire history (P3b).

A ``ScheduledRun`` is a cron-triggered job that runs an analysis on every ticker
in a watchlist, applies materiality gating, and notifies. The dispatcher (a beat
task firing every 60s) advances ``next_run_at`` and fans out the work. Each fire
produces one idempotent ``ScheduledRunHistory`` row keyed by ``fire_time_utc`` so
a beat restart can't double-dispatch.
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from .triggers import compute_next, is_valid_cron


class ScheduledRun(models.Model):
    DEGRADE = "degrade"
    SKIP = "skip"
    NOTIFY_ONLY = "notify_only"
    ON_BREACH_CHOICES = [
        (DEGRADE, "Degrade to a cheaper preset"),
        (SKIP, "Skip the run"),
        (NOTIFY_ONLY, "Run anyway and notify about the overage"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="scheduled_runs",
    )
    name = models.CharField(max_length=120)
    watchlist = models.ForeignKey(
        "watchlists.Watchlist",
        on_delete=models.CASCADE,
        related_name="scheduled_runs",
    )
    # Subset of personas to run; [] = all registered personas.
    personas = models.JSONField(default=list, blank=True)
    # Scheduled runs default to the cheap `frugal` preset (per-run cost matters
    # more for recurring jobs than one-off ad-hoc runs).
    model_preset = models.CharField(max_length=32, default="frugal")
    model_overrides = models.JSONField(default=dict, blank=True)
    # P4c: the immutable agent-graph version each child Run inherits. NULL ⇒ the
    # hardcoded council.py. PROTECT keeps the version pinned for the schedule's
    # lifetime. Copied onto every Run the dispatcher creates (schedules/tasks.py).
    graph_version = models.ForeignKey(
        "graphs.AgentGraphVersion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    cron_expression = models.CharField(max_length=64)  # e.g. "25 9 * * 1-5"
    timezone = models.CharField(max_length=64, default="America/New_York")
    # When True, skip NYSE non-trading days (weekends + holidays). v1 is NY-only.
    is_market_aware = models.BooleanField(default=True)

    cost_ceiling_usd = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True
    )
    on_breach = models.CharField(
        max_length=16, choices=ON_BREACH_CHOICES, default=DEGRADE
    )

    notification_channel = models.ForeignKey(
        "notifications.NotificationChannel",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="schedules",
    )

    # P3b paper auto-submit (opt-in, PAPER ONLY). When on, each ticker's Decision
    # becomes a BrokerOrder on ``auto_submit_broker_account`` and is — by default —
    # auto-confirmed via the ``scheduled_job`` gate (which hard-blocks live
    # accounts) and filled; ``auto_submit_draft_only`` instead leaves orders in
    # draft for manual review. ``max_orders_per_day`` / ``max_notional_per_day_usd``
    # are per-schedule safety caps (counted over a trailing 24h); a breach skips
    # the rest with an audit note. The toggle + ``PAPER_AUTO_SUBMIT_ENABLED`` are
    # the kill switches.
    auto_paper_submit = models.BooleanField(default=False)
    auto_submit_broker_account = models.ForeignKey(
        "brokers.BrokerAccount",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="auto_submit_schedules",
    )
    auto_submit_draft_only = models.BooleanField(default=False)
    max_orders_per_day = models.PositiveIntegerField(default=10)
    max_notional_per_day_usd = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("10000")
    )

    is_active = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} ({self.cron_expression} {self.timezone})"

    def reschedule(self, after=None) -> None:
        """Recompute ``next_run_at`` from the cron expression. No-op (clears the
        next fire) when inactive, or when the cron is invalid or never fires.

        A syntactically valid expression can still have no next date (``0 0 31 2
        *``); croniter raises for those, which used to escape as a 500 from the
        create/update endpoint after the row had already been written.
        """
        self.next_run_at = None
        if not (self.is_active and is_valid_cron(self.cron_expression)):
            return
        try:
            self.next_run_at = compute_next(
                self.cron_expression, self.timezone, after=after
            )
        except Exception:  # noqa: BLE001 — croniter/zoneinfo errors alike
            self.next_run_at = None


class ScheduledRunHistory(models.Model):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (RUNNING, "Running"),
        (DONE, "Done"),
        (FAILED, "Failed"),
        (SKIPPED, "Skipped"),
    ]

    scheduled_run = models.ForeignKey(
        ScheduledRun, on_delete=models.CASCADE, related_name="history"
    )
    # The scheduled minute this row represents. Unique per schedule → the
    # dispatcher's get_or_create on (scheduled_run, fire_time_utc) is the
    # idempotency guard against double-dispatch on beat restart.
    fire_time_utc = models.DateTimeField()
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    runs = models.ManyToManyField("runs.Run", related_name="schedule_history", blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=PENDING)
    estimated_cost_usd = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True
    )
    actual_cost_usd = models.DecimalField(
        max_digits=10, decimal_places=4, default=Decimal("0")
    )
    degraded_preset = models.CharField(max_length=32, blank=True, default="")
    materiality_decision = models.JSONField(default=dict, blank=True)
    notified_count = models.IntegerField(default=0)
    # P3b paper auto-submit: the BrokerOrders this fire created (for audit + the
    # trailing-24h cap count) and a summary of what was submitted / skipped / why.
    broker_orders = models.ManyToManyField(
        "brokers.BrokerOrder", related_name="scheduled_histories", blank=True
    )
    submit_decision = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["scheduled_run", "fire_time_utc"],
                name="uniq_schedule_fire_time",
            ),
        ]

    def __str__(self) -> str:
        return f"history(sr={self.scheduled_run_id} @ {self.fire_time_utc})"
