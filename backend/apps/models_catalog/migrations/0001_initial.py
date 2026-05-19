from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="ModelEntry",
            fields=[
                ("id", models.SlugField(max_length=128, primary_key=True, serialize=False)),
                ("provider", models.CharField(max_length=32)),
                ("display_name", models.CharField(max_length=128)),
                ("tier", models.CharField(choices=[
                    ("frontier", "Frontier"), ("fast_cheap", "Fast/Cheap"),
                    ("hosted_open", "Hosted Open"), ("local", "Local"),
                ], max_length=16)),
                ("context_window", models.IntegerField(default=200000)),
                ("supports_caching", models.BooleanField(default=False)),
                ("supports_structured_output", models.BooleanField(default=True)),
                ("supports_long_context", models.BooleanField(default=False)),
                ("price_in_per_mtok", models.DecimalField(blank=True, decimal_places=4, max_digits=10, null=True)),
                ("price_out_per_mtok", models.DecimalField(blank=True, decimal_places=4, max_digits=10, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("notes", models.TextField(blank=True, default="")),
            ],
        ),
        migrations.CreateModel(
            name="ProviderKey",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("anthropic_api_key_enc", models.TextField(blank=True, default="")),
                ("openrouter_api_key_enc", models.TextField(blank=True, default="")),
                ("openai_api_key_enc", models.TextField(blank=True, default="")),
                ("ollama_host", models.CharField(blank=True, default="", max_length=255)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                              related_name="provider_keys",
                                              to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="UserModelPreferences",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("preset", models.CharField(default="research", max_length=16)),
                ("per_agent_defaults", models.JSONField(blank=True, default=dict)),
                ("cost_ceiling_per_run_usd", models.DecimalField(blank=True, decimal_places=4,
                                                                  default=Decimal("5.0"),
                                                                  max_digits=10, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                              related_name="model_prefs",
                                              to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
