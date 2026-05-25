from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("runs", "0007_run_investor_profile_applied"),
    ]

    operations = [
        migrations.AddField(
            model_name="run",
            name="persona_evolution_applied",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
