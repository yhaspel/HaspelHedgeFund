from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('portfolios', '0011_pairs'),
    ]

    operations = [
        migrations.AddField(
            model_name='portfoliostrategy', name='enable_pair_council',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='portfoliostrategy', name='pair_council_min_confidence',
            field=models.DecimalField(max_digits=4, decimal_places=3, default=Decimal("0.500")),
        ),
        migrations.AddField(
            model_name='pair', name='council_action',
            field=models.CharField(max_length=12, blank=True, default=''),
        ),
        migrations.AddField(
            model_name='pair', name='council_confidence',
            field=models.FloatField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='pair', name='council_thesis',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='pair', name='council_votes',
            field=models.JSONField(default=list, blank=True),
        ),
    ]
