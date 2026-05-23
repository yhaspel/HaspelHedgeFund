"""P3a-1 follow-up: now that the brokers app exists, add the FKs on
LedgerEntry pointing to BrokerOrder / BrokerSyncEvent. Lives in the
portfolios app so it sits next to the field it adds.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("portfolios", "0023_broker_portfolio_kind"),
        ("brokers", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="ledgerentry",
            name="broker_order",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ledger_entries",
                to="brokers.brokerorder",
            ),
        ),
        migrations.AddField(
            model_name="ledgerentry",
            name="broker_sync_event",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ledger_entries",
                to="brokers.brokersyncevent",
            ),
        ),
    ]
