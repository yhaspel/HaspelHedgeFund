# P3-prereq-5 WS-G: per-strategy opt-in for investor-profile personalization.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('portfolios', '0021_cycle_marked_snapshot'),
    ]

    operations = [
        migrations.AddField(
            model_name='portfoliostrategy',
            name='apply_investor_profile',
            field=models.BooleanField(
                default=False,
                help_text=(
                    "When true and the owner's profile master switch is on, "
                    "this strategy's cycle Runs are personalized to the owner's "
                    "investor profile (CIO, personas and Risk Manager narrative only)."
                ),
            ),
        ),
    ]
