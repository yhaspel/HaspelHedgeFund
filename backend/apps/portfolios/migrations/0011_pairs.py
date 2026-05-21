from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('portfolios', '0010_risk_parity'),
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
                    ('pairs', 'Pairs trading (cointegration)'),
                ],
                default='long_short',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='portfoliostrategy', name='pair_entry_z',
            field=models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("2.0")),
        ),
        migrations.AddField(
            model_name='portfoliostrategy', name='pair_exit_z',
            field=models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("0.5")),
        ),
        migrations.AddField(
            model_name='portfoliostrategy', name='pair_stop_z',
            field=models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("4.0")),
        ),
        migrations.AddField(
            model_name='portfoliostrategy', name='pair_max_held',
            field=models.SmallIntegerField(default=8),
        ),
        migrations.AddField(
            model_name='portfoliostrategy', name='pair_notional_pct',
            field=models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.05")),
        ),
        migrations.AddField(
            model_name='portfoliostrategy', name='pair_cointegration_p_max',
            field=models.DecimalField(max_digits=4, decimal_places=3, default=Decimal("0.05")),
        ),
        migrations.AddField(
            model_name='portfoliostrategy', name='pair_lookback_days',
            field=models.SmallIntegerField(default=252),
        ),
        migrations.AddField(
            model_name='portfoliostrategy', name='pair_correlation_min',
            field=models.DecimalField(max_digits=4, decimal_places=3, default=Decimal("0.700")),
        ),
        migrations.CreateModel(
            name='Pair',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('leg_a_ticker', models.CharField(max_length=16)),
                ('leg_b_ticker', models.CharField(max_length=16)),
                ('sector', models.CharField(max_length=64, blank=True, default='')),
                ('cointegration_p_value', models.FloatField(default=1.0)),
                ('correlation', models.FloatField(default=0.0)),
                ('hedge_ratio', models.FloatField(default=1.0)),
                ('spread_mean', models.FloatField(default=0.0)),
                ('spread_std', models.FloatField(default=0.0)),
                ('spread_window_days', models.SmallIntegerField(default=252)),
                ('entry_date', models.DateField(null=True, blank=True)),
                ('entry_z', models.FloatField(null=True, blank=True)),
                ('exit_date', models.DateField(null=True, blank=True)),
                ('exit_z', models.FloatField(null=True, blank=True)),
                ('exit_reason', models.CharField(max_length=24, blank=True, default='')),
                ('status', models.CharField(
                    max_length=12,
                    choices=[('candidate', 'Candidate'), ('open', 'Open'), ('closed', 'Closed')],
                    default='candidate',
                )),
                ('notional_per_leg_usd',
                 models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('strategy', models.ForeignKey(
                    to='portfolios.portfoliostrategy',
                    related_name='pairs',
                    on_delete=models.deletion.CASCADE,
                )),
            ],
            options={'indexes': [models.Index(fields=['strategy', 'status'], name='portfolios__strateg_4d39ea_idx')]},
        ),
        migrations.AddField(
            model_name='rebalanceorder',
            name='pair',
            field=models.ForeignKey(
                to='portfolios.pair',
                null=True, blank=True,
                related_name='orders',
                on_delete=models.deletion.SET_NULL,
            ),
        ),
    ]
