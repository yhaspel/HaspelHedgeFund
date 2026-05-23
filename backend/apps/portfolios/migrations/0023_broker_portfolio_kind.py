"""P3a-1: extend Portfolio.kind with the broker option and add broker
ledger-entry kinds. The new LedgerEntry FKs to brokers.BrokerOrder /
brokers.BrokerSyncEvent are added by the brokers app's own migration
(0002_ledger_broker_fks) so this migration can run before brokers exists.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portfolios", "0022_strategy_apply_investor_profile"),
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
                ],
                db_index=True,
                default="strategy",
                max_length=12,
            ),
        ),
        migrations.AlterField(
            model_name="ledgerentry",
            name="kind",
            field=models.CharField(
                choices=[
                    ("deposit", "Cash deposit"),
                    ("withdrawal", "Cash withdrawal"),
                    ("position_open", "Position opened"),
                    ("position_increase", "Position increased"),
                    ("position_reduce", "Position reduced"),
                    ("position_close", "Position closed"),
                    ("edit_adjustment", "Manual edit adjustment"),
                    ("broker_fill", "Broker fill"),
                    ("reconciliation_adjustment", "Reconciliation adjustment"),
                ],
                max_length=32,
            ),
        ),
    ]
