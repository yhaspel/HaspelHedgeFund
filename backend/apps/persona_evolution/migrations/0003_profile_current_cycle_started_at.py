"""Add ``current_cycle_started_at`` so the frontend can poll for in-flight
progress and survive a page reload (P3-D follow-up)."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("persona_evolution", "0002_seed_personas"),
    ]

    operations = [
        migrations.AddField(
            model_name="personaevolutionprofile",
            name="current_cycle_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
