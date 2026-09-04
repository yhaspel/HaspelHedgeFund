# P14 — attribute a broker order to the fund sleeve it trades for, so fills on
# the shared fund account can be applied to the strategy's sleeve ledger too.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("brokers", "0006_p7_autonomous_fund"),
        ("portfolios", "0037_p14_shared_pool_fund"),
    ]

    operations = [
        migrations.AddField(
            model_name="brokerorder",
            name="sleeve",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="broker_orders", to="portfolios.fundsleeve",
            ),
        ),
    ]
