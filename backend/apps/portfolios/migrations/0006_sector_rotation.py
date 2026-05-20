from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('portfolios', '0005_concentrated_long'),
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
                ],
                default='long_short',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='portfoliostrategy',
            name='max_etfs_held',
            field=models.SmallIntegerField(default=6),
        ),
        migrations.AddField(
            model_name='portfoliostrategy',
            name='per_etf_max_pct',
            field=models.DecimalField(decimal_places=4, default=Decimal('0.30'), max_digits=5),
        ),
        migrations.AddField(
            model_name='portfoliostrategy',
            name='per_etf_min_pct',
            field=models.DecimalField(decimal_places=4, default=Decimal('0.05'), max_digits=5),
        ),
        migrations.CreateModel(
            name='SectorETF',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('ticker', models.CharField(max_length=16, unique=True)),
                ('sector', models.CharField(max_length=64)),
                ('theme', models.CharField(blank=True, default='', max_length=64)),
                ('issuer', models.CharField(default='SPDR', max_length=32)),
                ('aum_usd', models.BigIntegerField(default=0)),
                ('avg_daily_volume_usd', models.BigIntegerField(default=0)),
                ('expense_ratio_bps', models.SmallIntegerField(default=10)),
                ('is_active', models.BooleanField(default=True)),
                ('regime_affinities', models.JSONField(blank=True, default=dict)),
                ('description', models.TextField(blank=True, default='')),
            ],
        ),
    ]
