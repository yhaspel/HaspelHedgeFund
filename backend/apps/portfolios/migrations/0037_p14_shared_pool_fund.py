# P14 — shared-pool autonomous fund: ONE broker account per fund, membership as
# ``FundSleeve`` (allocation % + sleeve ledger) instead of the bare roster M2M.
#
# Data step: every existing fund's roster is carried over as sleeves with an
# equal split and an EMPTY ledger, the fund's shared account is left unset, and
# the members' autopilots are DISABLED. This is deliberate — the restructure is
# a fresh start (decision 2026-09-03): the owner picks the one shared account on
# the Fund tab, flattens/resets, then re-enables. Nothing may keep trading on
# the old one-account-per-strategy links under the new sleeve bridge.
from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


def _forwards(apps, schema_editor):
    AutonomousFund = apps.get_model("portfolios", "AutonomousFund")
    FundSleeve = apps.get_model("portfolios", "FundSleeve")
    Portfolio = apps.get_model("portfolios", "Portfolio")
    StrategyAutopilot = apps.get_model("portfolios", "StrategyAutopilot")

    for fund in AutonomousFund.objects.all():
        # Historical model: ``strategies`` is still the auto-through M2M here
        # (the field swap below runs after this step).
        members = list(fund.strategies.all().order_by("id"))
        if not members:
            continue
        n = len(members)
        base = (Decimal("100") / n).quantize(Decimal("0.01"))
        for i, strategy in enumerate(members):
            pct = base if i < n - 1 else (Decimal("100") - base * (n - 1))
            book = Portfolio.objects.create(
                user_id=fund.owner_id,
                name=f"Sleeve · {strategy.name}"[:64],
                kind="sleeve",
                cash_balance=Decimal("0"),
            )
            FundSleeve.objects.create(
                fund=fund, strategy=strategy, portfolio=book,
                allocation_pct=pct, initial_capital_usd=Decimal("0"), is_active=True,
            )
        # Stop-the-world for the restructure: members re-enable from the Fund tab
        # once the shared account is chosen and the sleeves are funded (Reset).
        StrategyAutopilot.objects.filter(strategy__in=members).update(
            is_enabled=False, next_run_at=None,
        )


def _backwards(apps, schema_editor):
    # The roster M2M is re-created empty by the reversed field swap; re-fill it
    # from the sleeves so a rollback keeps the membership (ledgers are dropped).
    AutonomousFund = apps.get_model("portfolios", "AutonomousFund")
    FundSleeve = apps.get_model("portfolios", "FundSleeve")
    for sleeve in FundSleeve.objects.select_related("fund"):
        AutonomousFund.strategies.through.objects.get_or_create(
            autonomousfund_id=sleeve.fund_id, portfoliostrategy_id=sleeve.strategy_id,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("brokers", "0006_p7_autonomous_fund"),
        ("portfolios", "0036_alter_portfoliostrategy_kind"),
    ]

    operations = [
        migrations.AlterField(
            model_name="portfolio",
            name="kind",
            field=models.CharField(
                choices=[
                    ("strategy", "Strategy"),
                    ("manual", "Manual"),
                    ("broker", "Broker"),
                    ("sleeve", "Fund sleeve"),
                ],
                db_index=True,
                default="strategy",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="autonomousfund",
            name="broker_account",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="funds", to="brokers.brokeraccount",
            ),
        ),
        migrations.CreateModel(
            name="FundSleeve",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("allocation_pct", models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=6)),
                ("initial_capital_usd", models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=14)),
                ("is_active", models.BooleanField(default=True)),
                ("removed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("fund", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sleeves", to="portfolios.autonomousfund")),
                ("portfolio", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="fund_sleeve", to="portfolios.portfolio")),
                ("strategy", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="fund_sleeve", to="portfolios.portfoliostrategy")),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("fund", "strategy"), name="uniq_sleeve_per_fund"),
                ],
            },
        ),
        migrations.RunPython(_forwards, _backwards),
        # Swap the roster M2M for the through model. The auto-created join table
        # is dropped for real; the new field is state-only (its through table —
        # FundSleeve — already exists above).
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RemoveField(model_name="autonomousfund", name="strategies"),
            ],
            state_operations=[
                migrations.RemoveField(model_name="autonomousfund", name="strategies"),
                migrations.AddField(
                    model_name="autonomousfund",
                    name="strategies",
                    field=models.ManyToManyField(
                        blank=True, related_name="funds",
                        through="portfolios.FundSleeve", to="portfolios.portfoliostrategy",
                    ),
                ),
            ],
        ),
    ]
