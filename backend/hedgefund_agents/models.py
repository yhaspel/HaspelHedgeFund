"""Persistence for LLM telemetry. Every call goes here."""
from decimal import Decimal

from django.db import models

from .versioning import AgentVersion  # noqa: F401 — register model with Django


class LLMCall(models.Model):
    run = models.ForeignKey(
        "runs.Run", related_name="llm_calls", on_delete=models.CASCADE, null=True, blank=True
    )
    backtest = models.ForeignKey(
        "backtests.Backtest", related_name="llm_calls", on_delete=models.CASCADE,
        null=True, blank=True,
    )
    agent_name = models.CharField(max_length=64)
    provider = models.CharField(max_length=32)  # "anthropic", "openrouter"
    model = models.CharField(max_length=128)
    prompt_tokens = models.IntegerField(default=0)
    cached_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=Decimal("0"))
    latency_ms = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.provider}:{self.model} {self.agent_name}"
