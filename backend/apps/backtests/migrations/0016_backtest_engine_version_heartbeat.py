"""Engine v2 stamp + worker heartbeat.

``engine_version`` records which simulation engine produced a row's numbers.
Every row that exists when this migration runs was computed by engine v1 (fresh
book per council fold, financing on the cash debit only, raw-close split
inference), so they are stamped 1 and keep their stored metrics; new rows
default to 2.

``heartbeat_at`` is written on every progress update so the orphan sweeper can
tell a slow-but-alive run from an abandoned one.
"""
from decimal import Decimal

import django.core.validators
from django.db import migrations, models


def stamp_existing_rows_engine_v1(apps, schema_editor):
    Backtest = apps.get_model("backtests", "Backtest")
    Backtest.objects.all().update(engine_version=1)


class Migration(migrations.Migration):
    dependencies = [
        ("backtests", "0015_alter_backtest_engine_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="backtest",
            name="engine_version",
            field=models.IntegerField(default=2),
        ),
        migrations.AddField(
            model_name="backtest",
            name="heartbeat_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(
            stamp_existing_rows_engine_v1, migrations.RunPython.noop
        ),
        # Declared floors on the walk-forward / cost fields (the create
        # serializer is the enforcing boundary; these only bite under
        # full_clean(), and no stored row is rewritten).
        migrations.AlterField(
            model_name="backtest",
            name="commission_bps",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("5"),
                max_digits=6,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.AlterField(
            model_name="backtest",
            name="financing_bps",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("200"),
                max_digits=6,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.AlterField(
            model_name="backtest",
            name="is_window_days",
            field=models.IntegerField(
                default=252, validators=[django.core.validators.MinValueValidator(126)]
            ),
        ),
        migrations.AlterField(
            model_name="backtest",
            name="max_budget_usd",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("4.00"),
                max_digits=8,
                validators=[django.core.validators.MinValueValidator(Decimal("0.05"))],
            ),
        ),
        migrations.AlterField(
            model_name="backtest",
            name="n_candidates",
            field=models.IntegerField(
                default=50, validators=[django.core.validators.MinValueValidator(1)]
            ),
        ),
        migrations.AlterField(
            model_name="backtest",
            name="oos_window_days",
            field=models.IntegerField(
                default=63, validators=[django.core.validators.MinValueValidator(21)]
            ),
        ),
        migrations.AlterField(
            model_name="backtest",
            name="rebalance_frequency",
            field=models.CharField(
                choices=[
                    ("daily", "Daily"),
                    ("weekly", "Weekly"),
                    ("monthly", "Monthly"),
                ],
                default="weekly",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="backtest",
            name="spread_bps",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("5"),
                max_digits=6,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.AlterField(
            model_name="backtest",
            name="starting_cash",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("100000"),
                max_digits=18,
                validators=[django.core.validators.MinValueValidator(Decimal("0.01"))],
            ),
        ),
        migrations.AlterField(
            model_name="backtest",
            name="step_days",
            field=models.IntegerField(
                default=63, validators=[django.core.validators.MinValueValidator(1)]
            ),
        ),
    ]
