"""P4 fix: per-account default whole/fractional order quantity mode."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("brokers", "0003_alter_brokeraccount_id_alter_brokercredential_id_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="brokeraccount",
            name="default_quantity_mode",
            field=models.CharField(
                choices=[("whole", "Whole shares"), ("fractional", "Fractional")],
                default="whole",
                max_length=10,
            ),
        ),
    ]
