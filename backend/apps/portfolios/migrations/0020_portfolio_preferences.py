"""P3: per-user Manual Book preferences (mark cadence + interval)."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("portfolios", "0019_p3_manual_book"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PortfolioPreferences",
            fields=[
                ("id", models.BigAutoField(
                    auto_created=True, primary_key=True, serialize=False,
                )),
                ("mark_cadence", models.CharField(
                    choices=[
                        ("daily", "Daily"),
                        ("delayed", "Delayed (auto-refresh)"),
                        ("manual", "Pull only (manual refresh)"),
                    ],
                    default="daily",
                    max_length=12,
                )),
                ("interval_minutes", models.PositiveSmallIntegerField(default=20)),
                ("last_refreshed_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="portfolio_preferences",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
        ),
    ]
