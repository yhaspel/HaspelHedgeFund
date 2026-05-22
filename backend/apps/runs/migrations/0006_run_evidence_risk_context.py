from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("runs", "0005_run_source_target"),
    ]
    operations = [
        migrations.AddField(
            model_name="run",
            name="evidence",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="run",
            name="risk_context",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
