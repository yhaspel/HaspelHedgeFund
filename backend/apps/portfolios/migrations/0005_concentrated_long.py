from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('portfolios', '0004_market_neutral'),
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
                ],
                default='long_short',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='portfoliostrategy',
            name='max_positions',
            field=models.SmallIntegerField(default=15),
        ),
        migrations.AddField(
            model_name='portfoliostrategy',
            name='min_positions',
            field=models.SmallIntegerField(default=5),
        ),
        migrations.AddField(
            model_name='portfoliostrategy',
            name='min_aggregate_confidence',
            field=models.DecimalField(decimal_places=3, default=Decimal('0.650'), max_digits=4),
        ),
        migrations.AddField(
            model_name='portfoliotarget',
            name='per_position_thesis',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='portfoliotarget',
            name='cycle_outcome',
            field=models.CharField(blank=True, default='', max_length=24),
        ),
    ]
