"""Initial migration: PersonaEvolutionProfile, PersonaEvolutionRevision,
PersonaEvolutionSettings (P3-D WS-A)."""
from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("hedgefund_agents", "0004_llmcall_portfolio_target"),
    ]

    operations = [
        migrations.CreateModel(
            name="PersonaEvolutionProfile",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("persona_name", models.CharField(max_length=32, unique=True)),
                ("display_name", models.CharField(max_length=64)),
                ("firm_name", models.CharField(blank=True, default="", max_length=128)),
                ("search_aliases", models.JSONField(blank=True, default=list)),
                ("is_evolvable", models.BooleanField(default=True)),
                ("lifecycle_note", models.CharField(blank=True, default="", max_length=256)),
                ("last_cycle_at", models.DateTimeField(blank=True, null=True)),
                (
                    "last_cycle_status",
                    models.CharField(
                        choices=[
                            ("never", "Never"),
                            ("ok", "Ok"),
                            ("skipped", "Skipped"),
                            ("failed", "Failed"),
                        ],
                        default="never",
                        max_length=12,
                    ),
                ),
                ("last_cycle_note", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["persona_name"]},
        ),
        migrations.CreateModel(
            name="PersonaEvolutionRevision",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("seq", models.PositiveIntegerField()),
                ("as_of_date", models.DateField()),
                ("market_stance_md", models.TextField(blank=True, default="")),
                ("general_notes_md", models.TextField(blank=True, default="")),
                ("char_count", models.PositiveIntegerField(default=0)),
                ("over_budget", models.BooleanField(default=False)),
                ("material_change", models.BooleanField(default=True)),
                ("dropped_facts", models.JSONField(blank=True, default=list)),
                ("source_urls", models.JSONField(blank=True, default=list)),
                ("raw_inputs", models.JSONField(blank=True, default=dict)),
                ("model_id", models.CharField(blank=True, default="", max_length=128)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="revisions",
                        to="persona_evolution.personaevolutionprofile",
                    ),
                ),
                (
                    "llm_call",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="hedgefund_agents.llmcall",
                    ),
                ),
            ],
            options={
                "ordering": ["-seq"],
                "unique_together": {("profile", "seq")},
                "indexes": [
                    models.Index(
                        fields=["profile", "-as_of_date"],
                        name="persona_evo_profile_4ad6c7_idx",
                    )
                ],
            },
        ),
        migrations.AddField(
            model_name="personaevolutionprofile",
            name="current_revision",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="persona_evolution.personaevolutionrevision",
            ),
        ),
        migrations.CreateModel(
            name="PersonaEvolutionSettings",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("enabled", models.BooleanField(default=False)),
                (
                    "cadence",
                    models.CharField(
                        choices=[
                            ("off", "Off"),
                            ("daily", "Daily"),
                            ("weekly", "Weekly"),
                            ("monthly", "Monthly"),
                        ],
                        default="off",
                        max_length=8,
                    ),
                ),
                ("model_id", models.CharField(blank=True, default="", max_length=128)),
                ("web_search_enabled", models.BooleanField(default=True)),
                (
                    "monthly_cost_cap_usd",
                    models.DecimalField(
                        decimal_places=2, default=Decimal("2.00"), max_digits=8
                    ),
                ),
                ("cost_cap_reached_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="persona_evolution_settings",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
    ]
