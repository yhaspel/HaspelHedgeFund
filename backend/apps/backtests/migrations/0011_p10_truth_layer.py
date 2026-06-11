# P10 §B — truth layer: data-era tag (B3), legacy-baseline preservation +
# benchmark block (B1/B2).
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("backtests", "0010_alter_backtest_engine_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="backtest",
            name="data_era",
            field=models.CharField(
                choices=[
                    ("price_only", "Price-only data (pre PR #50)"),
                    ("total_return", "Total-return data"),
                ],
                default="total_return",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="backtestmetrics",
            name="baseline_return_pct_legacy",
            field=models.DecimalField(
                blank=True, decimal_places=4, max_digits=10, null=True,
            ),
        ),
        migrations.AddField(
            model_name="backtestmetrics",
            name="benchmarks",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
