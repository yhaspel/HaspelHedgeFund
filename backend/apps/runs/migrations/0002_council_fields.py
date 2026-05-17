from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("runs", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="run",
            name="personas",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="run",
            name="agent_versions",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="decision",
            name="target_quantity",
            field=models.DecimalField(decimal_places=6, default=Decimal("0"), max_digits=18),
        ),
        migrations.AddField(
            model_name="decision",
            name="target_weight_pct",
            field=models.DecimalField(decimal_places=4, default=Decimal("0"), max_digits=6),
        ),
        migrations.AddField(
            model_name="decision",
            name="risk_overrides",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
