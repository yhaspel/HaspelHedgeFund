"""Reviews-driven hardening landing in one migration:

  - P02g: concentrated_long defaults aligned with plan/UI (min_positions
    3→5, min_aggregate_confidence 0.55→0.65).
  - P02g: ``cycle_outcome`` max_length 24→32 to fit the documented enum.
  - P02h: ``ETFHoldingSnapshot`` model so sector overlap is computed from
    real holdings rather than a hardcoded pair map.

Existing rows are left untouched. The new model is independent — empty
table → cycle falls back to the legacy hardcoded overlap pairs.
"""

from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("portfolios", "0017_p2m_markov_regime"),
    ]

    operations = [
        migrations.AlterField(
            model_name="portfoliostrategy",
            name="min_positions",
            field=models.SmallIntegerField(default=5),
        ),
        migrations.AlterField(
            model_name="portfoliostrategy",
            name="min_aggregate_confidence",
            field=models.DecimalField(
                decimal_places=3, default=Decimal("0.650"), max_digits=4,
            ),
        ),
        migrations.AlterField(
            model_name="portfoliotarget",
            name="cycle_outcome",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.CreateModel(
            name="ETFHoldingSnapshot",
            fields=[
                ("id", models.BigAutoField(
                    auto_created=True, primary_key=True, serialize=False,
                )),
                ("etf_ticker", models.CharField(db_index=True, max_length=16)),
                ("constituent_ticker", models.CharField(db_index=True, max_length=16)),
                ("as_of_date", models.DateField(db_index=True)),
                ("weight", models.DecimalField(decimal_places=6, max_digits=7)),
                ("source", models.CharField(default="manual", max_length=32)),
                ("fetched_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["etf_ticker", "as_of_date"],
                        name="portf_etfsnap_idx",
                    ),
                ],
                "unique_together": {
                    ("etf_ticker", "constituent_ticker", "as_of_date", "source"),
                },
            },
        ),
    ]
