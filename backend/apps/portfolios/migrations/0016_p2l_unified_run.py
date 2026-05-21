from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portfolios", "0015_hedge_ratio_drift"),
        ("runs", "0005_run_source_target"),
    ]

    operations = [
        # 1. auto_run_council toggle on strategy.
        migrations.AddField(
            model_name="portfoliostrategy",
            name="auto_run_council",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "When true, dispatch council tasks immediately after "
                    "screening. When false, stop at awaiting_review until the "
                    "user approves the screened candidates."
                ),
            ),
        ),
        # 2. Expanded PortfolioTarget.STATUS_CHOICES (add new statuses).
        migrations.AlterField(
            model_name="portfoliotarget",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("screening", "Screening"),
                    ("awaiting_review", "Awaiting review"),
                    ("running_council", "Running council"),
                    ("constructing", "Constructing"),
                    ("running", "Running"),
                    ("done", "Done"),
                    ("failed", "Failed"),
                    ("cancelled", "Cancelled"),
                ],
                default="queued",
                max_length=20,
            ),
        ),
        # 3. Replace unique_strategy_target_per_day with a partial one that
        #    excludes cancelled rows (same-day reruns must be possible after a
        #    rejected cycle).
        migrations.RemoveConstraint(
            model_name="portfoliotarget",
            name="uniq_strategy_target_per_day",
        ),
        migrations.AddConstraint(
            model_name="portfoliotarget",
            constraint=models.UniqueConstraint(
                fields=["strategy", "as_of_date"],
                condition=~models.Q(status="cancelled"),
                name="uniq_active_strategy_target_per_day",
            ),
        ),
        # 4. PortfolioTargetRun through-model: one row per (target, candidate).
        migrations.CreateModel(
            name="PortfolioTargetRun",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                ("candidate_key", models.CharField(max_length=64)),
                ("primary_ticker", models.CharField(blank=True, default="", max_length=16)),
                ("side", models.CharField(max_length=12)),
                ("borrow_veto", models.BooleanField(default=False)),
                ("screener_rank", models.PositiveIntegerField()),
                ("screener_score", models.FloatField(blank=True, null=True)),
                ("sector", models.CharField(blank=True, default="", max_length=64)),
                ("candidate_payload", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "target",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="candidate_run_links",
                        to="portfolios.portfoliotarget",
                    ),
                ),
                (
                    "run",
                    models.OneToOneField(
                        on_delete=models.CASCADE,
                        related_name="portfolio_target_run_link",
                        to="runs.run",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=["target", "candidate_key", "side"],
                        name="uniq_target_candidate_side",
                    ),
                    models.UniqueConstraint(
                        fields=["target", "run"],
                        name="uniq_target_candidate_run",
                    ),
                ],
                "indexes": [
                    models.Index(
                        fields=["target", "screener_rank"],
                        name="ptr_target_rank_idx",
                    ),
                    models.Index(
                        fields=["target", "candidate_key"],
                        name="ptr_target_key_idx",
                    ),
                ],
            },
        ),
    ]
