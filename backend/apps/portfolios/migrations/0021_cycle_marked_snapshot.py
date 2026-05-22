"""P3 addendum: per-cycle marked snapshot on ``PortfolioTarget``.

Treats ``target_weights`` as a hypothetical-hold book and persists a
mark-to-market snapshot (per-ticker return since ``as_of_date`` + book
gross/net/return). Feeds the strategy-detail UI today and the P3b
decisions-based leaderboard later, without requiring broker fills.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("portfolios", "0020_portfolio_preferences"),
    ]

    operations = [
        migrations.AddField(
            model_name="portfoliotarget",
            name="marked_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
