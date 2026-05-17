from decimal import Decimal

from django.conf import settings
from django.db import models


class Run(models.Model):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (QUEUED, "Queued"),
        (RUNNING, "Running"),
        (DONE, "Done"),
        (FAILED, "Failed"),
        (CANCELLED, "Cancelled"),
    ]
    ACTIVE_STATUSES = {QUEUED, RUNNING}

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="runs", on_delete=models.CASCADE
    )
    tickers = models.JSONField(default=list)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=QUEUED)
    model_overrides = models.JSONField(default=dict, blank=True)
    as_of_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    total_cost_usd = models.DecimalField(
        max_digits=10, decimal_places=6, default=Decimal("0")
    )
    error_message = models.TextField(blank=True, default="")
    # Subset of personas to run; empty/None = run all registered.
    personas = models.JSONField(default=list, blank=True)
    # {agent_name: version} snapshot of which versions executed.
    agent_versions = models.JSONField(default=dict, blank=True)
    # Celery task id, so /cancel/ can revoke + terminate the worker subprocess.
    celery_task_id = models.CharField(max_length=64, blank=True, default="")

    def __str__(self) -> str:
        return f"Run {self.pk} ({self.status})"


class AgentMessage(models.Model):
    run = models.ForeignKey(Run, related_name="messages", on_delete=models.CASCADE)
    agent_name = models.CharField(max_length=64)
    raw_response = models.TextField(blank=True, default="")
    parsed_output = models.JSONField(default=dict)
    status = models.CharField(max_length=16, default="ok")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.agent_name} run={self.run_id}"


class Decision(models.Model):
    run = models.ForeignKey(Run, related_name="decisions", on_delete=models.CASCADE)
    ticker = models.CharField(max_length=16)
    action = models.CharField(max_length=8)
    confidence = models.IntegerField(default=0)
    rationale = models.TextField(blank=True, default="")
    dissenting_views = models.JSONField(default=list)
    target_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("0"))
    target_weight_pct = models.DecimalField(max_digits=6, decimal_places=4, default=Decimal("0"))
    risk_overrides = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.ticker} {self.action} run={self.run_id}"
