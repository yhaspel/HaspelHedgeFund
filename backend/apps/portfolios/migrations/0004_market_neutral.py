from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('portfolios', '0003_portfoliostrategy_kind'),
    ]

    operations = [
        migrations.AddField(
            model_name='portfoliostrategy',
            name='benchmark_ticker',
            field=models.CharField(default='SPY', max_length=16),
        ),
        migrations.AddField(
            model_name='portfoliostrategy',
            name='beta_window_days',
            field=models.SmallIntegerField(default=252),
        ),
        migrations.AddField(
            model_name='portfoliostrategy',
            name='neutrality_tolerance_dollar_pct',
            field=models.DecimalField(decimal_places=4, default=Decimal('0.02'), max_digits=5),
        ),
        migrations.AddField(
            model_name='portfoliostrategy',
            name='neutrality_tolerance_beta',
            field=models.DecimalField(decimal_places=4, default=Decimal('0.05'), max_digits=5),
        ),
        migrations.AddField(
            model_name='portfoliostrategy',
            name='drop_on_unreliable_beta',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='portfoliotarget',
            name='realised_net_pct',
            field=models.DecimalField(decimal_places=4, default=Decimal('0'), max_digits=6),
        ),
        migrations.AddField(
            model_name='portfoliotarget',
            name='realised_portfolio_beta',
            field=models.DecimalField(decimal_places=3, default=Decimal('0'), max_digits=6),
        ),
        migrations.AddField(
            model_name='portfoliotarget',
            name='beta_diagnostics',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.CreateModel(
            name='BetaEstimate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ticker', models.CharField(db_index=True, max_length=16)),
                ('benchmark', models.CharField(db_index=True, default='SPY', max_length=16)),
                ('as_of_date', models.DateField(db_index=True)),
                ('window_days', models.SmallIntegerField(default=252)),
                ('beta', models.DecimalField(decimal_places=3, max_digits=6)),
                ('r_squared', models.DecimalField(decimal_places=3, max_digits=5)),
                ('n_observations', models.SmallIntegerField()),
                ('reliable', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'unique_together': {('ticker', 'benchmark', 'as_of_date', 'window_days')},
                'indexes': [models.Index(fields=['ticker', 'as_of_date'], name='portfolios__ticker_46edda_idx')],
            },
        ),
    ]
