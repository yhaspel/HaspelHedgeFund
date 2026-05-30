"""P3b council-alpha: capture the council-free deterministic baseline book
per cycle so the nightly leaderboard can measure realised − baseline.

Surgical: adds only the three baseline fields on PortfolioTarget. (Unrelated
index-name/AutoField drift that makemigrations also detected is intentionally
left out — it's pre-existing and orthogonal to this change.)
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portfolios", "0025_remove_portfoliotarget_uniq_active_strategy_target_per_day_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="portfoliotarget",
            name="baseline_weights",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="portfoliotarget",
            name="baseline_marked_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="portfoliotarget",
            name="baseline_version",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
    ]
