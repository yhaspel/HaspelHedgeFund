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

    ADHOC = "adhoc"
    STRATEGY = "strategy"
    SOURCE_CHOICES = [
        (ADHOC, "Ad-hoc"),
        (STRATEGY, "Strategy cycle"),
    ]

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
    # P2l: ad-hoc runs vs strategy-cycle-sourced runs. Strategy-sourced runs
    # carry portfolio_target back-link so the UI can deep-link both directions.
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default=ADHOC)
    portfolio_target = models.ForeignKey(
        "portfolios.PortfolioTarget",
        null=True,
        blank=True,
        related_name="strategy_runs",
        on_delete=models.SET_NULL,
        db_index=True,
    )

    # P01 review: first-class evidence/provenance for run outputs.
    # Structure: {"items": [{"agent": str, "provider": str, "source": str,
    #             "url": str, "as_of": iso-date, "retrieved_at": iso-ts,
    #             "label": str, "note": str}], "providers": {...}}
    evidence = models.JSONField(default=dict, blank=True)

    # P02a review: explicit risk-context label so the UI can show whether
    # sizing is illustrative (stub), portfolio-aware (real), or research-only.
    # Structure: {"mode": "stub"|"real"|"research_only",
    #             "portfolio_id": int|null, "notes": str,
    #             "stub_nav_usd": Decimal-as-string}
    risk_context = models.JSONField(default=dict, blank=True)

    # P3-prereq-5 WS-C: audit snapshot of the investor-profile injection.
    # Structure: {"applied": bool, "response_id": int|null,
    #             "schema_version": int|null, "agent_brief": str,
    #             "reason": ""|"no_profile"|"personalization_off"|"strategy_opt_out"}.
    # Empty {} for every existing run and every backtest.
    investor_profile_applied = models.JSONField(default=dict, blank=True)

    # P3-D WS-D: audit snapshot of which persona-evolution revision (if any)
    # was injected into each persona's user message for this run.
    # Structure: {"applied": bool,
    #             "as_of_date": iso-date,
    #             "revisions": {persona_name: seq}}.
    # Empty {} for every existing run, every backtest, and any run where
    # no persona had an applicable revision.
    persona_evolution_applied = models.JSONField(default=dict, blank=True)

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
    side = models.CharField(max_length=8, default="long")  # "long" | "short"
    target_weight_signed = models.DecimalField(
        max_digits=7, decimal_places=4, default=Decimal("0")
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.ticker} {self.action} run={self.run_id}"
