from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('portfolios', '0014_pair_z_history'),
    ]

    operations = [
        migrations.AddField(
            model_name='pair', name='hedge_ratio_drift_pct',
            field=models.FloatField(null=True, blank=True),
        ),
    ]
