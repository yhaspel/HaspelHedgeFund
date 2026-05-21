from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('portfolios', '0009_global_macro'),
    ]

    operations = [
        migrations.AlterField(
            model_name='portfoliostrategy',
            name='kind',
            field=models.CharField(
                choices=[
                    ('long_only', 'Long-only'),
                    ('short_only', 'Short-only'),
                    ('long_short', 'Long/Short'),
                    ('market_neutral', 'Market-neutral'),
                    ('concentrated_long', 'Concentrated long-only'),
                    ('sector_rotation', 'Sector / thematic ETF rotation'),
                    ('global_macro', 'Global macro (ETF expression)'),
                    ('risk_parity', 'Risk-parity / multi-asset lite'),
                ],
                default='long_short',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='portfoliostrategy',
            name='vol_window_days',
            field=models.SmallIntegerField(default=60),
        ),
        migrations.AddField(
            model_name='portfoliostrategy',
            name='rebalance_band_pct',
            field=models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.05")),
        ),
        migrations.AddField(
            model_name='portfoliostrategy',
            name='enable_council_veto',
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name='VolEstimate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('ticker', models.CharField(max_length=16, db_index=True)),
                ('as_of_date', models.DateField(db_index=True)),
                ('window_days', models.SmallIntegerField(default=60)),
                ('daily_vol', models.DecimalField(max_digits=8, decimal_places=6)),
                ('annualised_vol', models.DecimalField(max_digits=8, decimal_places=6)),
                ('n_observations', models.SmallIntegerField()),
                ('synthetic', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'unique_together': {('ticker', 'as_of_date', 'window_days')},
            },
        ),
    ]
