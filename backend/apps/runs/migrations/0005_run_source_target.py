from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("runs", "0004_decision_side"),
        ("portfolios", "0015_hedge_ratio_drift"),
    ]
    operations = [
        migrations.AddField(
            model_name="run",
            name="source",
            field=models.CharField(
                choices=[("adhoc", "Ad-hoc"), ("strategy", "Strategy cycle")],
                default="adhoc",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="run",
            name="portfolio_target",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="strategy_runs",
                to="portfolios.portfoliotarget",
            ),
        ),
    ]
