"""P3: Manual Book — Portfolio.kind + uniqueness, Position provenance + realized_pnl,
LedgerEntry model. Backfills existing Portfolio rows to kind='strategy' and existing
Position rows to opened_via='strategy_cycle'.
"""

from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def backfill_existing_rows(apps, schema_editor):
    Portfolio = apps.get_model("portfolios", "Portfolio")
    Position = apps.get_model("portfolios", "Position")
    Portfolio.objects.filter(kind="").update(kind="strategy")
    # Default for existing positions: they belonged to a strategy book.
    Position.objects.filter(opened_via="").update(opened_via="strategy_cycle")


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("portfolios", "0018_concentrated_long_defaults"),
        ("runs", "0006_run_evidence_risk_context"),  # Run + Decision FKs target apps.runs
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="portfolio",
            name="kind",
            field=models.CharField(
                choices=[("strategy", "Strategy"), ("manual", "Manual")],
                db_index=True,
                default="strategy",
                max_length=12,
            ),
        ),
        migrations.AddConstraint(
            model_name="portfolio",
            constraint=models.UniqueConstraint(
                condition=models.Q(("kind", "manual")),
                fields=("user",),
                name="uniq_manual_portfolio_per_user",
            ),
        ),
        migrations.AddField(
            model_name="position",
            name="opened_via",
            field=models.CharField(
                choices=[
                    ("manual", "Manual entry"),
                    ("run", "From run decision"),
                    ("strategy_cycle", "Strategy cycle"),
                ],
                default="manual",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="position",
            name="source_run",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="opened_positions",
                to="runs.run",
            ),
        ),
        migrations.AddField(
            model_name="position",
            name="source_decision",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="opened_positions",
                to="runs.decision",
            ),
        ),
        migrations.AddField(
            model_name="position",
            name="note",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="position",
            name="realized_pnl",
            field=models.DecimalField(
                decimal_places=2, default=Decimal("0"), max_digits=14,
            ),
        ),
        migrations.CreateModel(
            name="LedgerEntry",
            fields=[
                ("id", models.BigAutoField(
                    auto_created=True, primary_key=True, serialize=False,
                )),
                ("kind", models.CharField(
                    choices=[
                        ("deposit", "Cash deposit"),
                        ("withdrawal", "Cash withdrawal"),
                        ("position_open", "Position opened"),
                        ("position_increase", "Position increased"),
                        ("position_reduce", "Position reduced"),
                        ("position_close", "Position closed"),
                        ("edit_adjustment", "Manual edit adjustment"),
                    ],
                    max_length=24,
                )),
                ("ticker", models.CharField(blank=True, default="", max_length=16)),
                ("quantity_delta", models.DecimalField(
                    decimal_places=6, default=Decimal("0"), max_digits=18,
                )),
                ("price", models.DecimalField(
                    blank=True, decimal_places=4, max_digits=12, null=True,
                )),
                ("cash_delta", models.DecimalField(decimal_places=2, max_digits=14)),
                ("realized_pnl", models.DecimalField(
                    decimal_places=2, default=Decimal("0"), max_digits=14,
                )),
                ("quantity_after", models.DecimalField(
                    blank=True, decimal_places=6, max_digits=18, null=True,
                )),
                ("cash_balance_after", models.DecimalField(
                    decimal_places=2, max_digits=14,
                )),
                ("note", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("created_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="ledger_entries",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("portfolio", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="ledger",
                    to="portfolios.portfolio",
                )),
                ("position", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="ledger_entries",
                    to="portfolios.position",
                )),
                ("source_decision", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="ledger_entries",
                    to="runs.decision",
                )),
                ("source_run", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="ledger_entries",
                    to="runs.run",
                )),
            ],
            options={
                "ordering": ["-created_at", "-id"],
                "indexes": [
                    models.Index(
                        fields=["portfolio", "-created_at"],
                        name="portf_ledger_portf_ts_idx",
                    ),
                ],
            },
        ),
        migrations.RunPython(backfill_existing_rows, noop_reverse),
    ]
