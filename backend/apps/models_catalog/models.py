from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from .crypto import decrypt, encrypt


class ModelEntry(models.Model):
    TIER_CHOICES = [
        ("frontier", "Frontier"),
        ("fast_cheap", "Fast/Cheap"),
        ("hosted_open", "Hosted Open"),
        ("local", "Local"),
    ]
    id = models.SlugField(primary_key=True, max_length=128)
    provider = models.CharField(max_length=32)
    display_name = models.CharField(max_length=128)
    tier = models.CharField(max_length=16, choices=TIER_CHOICES)
    context_window = models.IntegerField(default=200_000)
    supports_caching = models.BooleanField(default=False)
    supports_structured_output = models.BooleanField(default=True)
    supports_long_context = models.BooleanField(default=False)
    price_in_per_mtok = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True
    )
    price_out_per_mtok = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True
    )
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default="")
    last_verified_at = models.DateTimeField(null=True, blank=True)
    last_verified_note = models.TextField(blank=True, default="")

    def __str__(self) -> str:
        return self.display_name

    @property
    def is_free(self) -> bool:
        return (
            (self.price_in_per_mtok is None or self.price_in_per_mtok == 0)
            and (self.price_out_per_mtok is None or self.price_out_per_mtok == 0)
        )


class ProviderKey(models.Model):
    """User's BYO keys, stored Fernet-encrypted. P4a → multi-tenant vault."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, related_name="provider_keys", on_delete=models.CASCADE
    )
    anthropic_api_key_enc = models.TextField(blank=True, default="")
    openrouter_api_key_enc = models.TextField(blank=True, default="")
    openai_api_key_enc = models.TextField(blank=True, default="")
    ollama_host = models.CharField(max_length=255, blank=True, default="")
    fmp_api_key_enc = models.TextField(blank=True, default="")
    tiingo_api_key_enc = models.TextField(blank=True, default="")
    fred_api_key_enc = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"keys u={self.user_id}"

    def set_key(self, provider: str, value: str | None) -> None:
        field = f"{provider}_api_key_enc"
        if not hasattr(self, field):
            raise ValueError(f"unknown provider {provider}")
        setattr(self, field, encrypt(value) if value else "")

    def get_key(self, provider: str) -> str:
        return decrypt(getattr(self, f"{provider}_api_key_enc", ""))

    def has_key(self, provider: str) -> bool:
        return bool(getattr(self, f"{provider}_api_key_enc", ""))


class UserModelPreferences(models.Model):
    PRESETS = ["dev", "research", "quality", "frugal", "hybrid"]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, related_name="model_prefs", on_delete=models.CASCADE
    )
    preset = models.CharField(max_length=16, default="research")
    per_agent_defaults = models.JSONField(default=dict, blank=True)
    cost_ceiling_per_run_usd = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True, default=Decimal("5.0")
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"prefs u={self.user_id} preset={self.preset}"
