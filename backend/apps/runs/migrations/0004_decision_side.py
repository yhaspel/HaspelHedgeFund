from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("runs", "0003_run_cancel")]
    operations = [
        migrations.AddField(
            model_name="decision",
            name="side",
            field=models.CharField(default="long", max_length=8),
        ),
        migrations.AddField(
            model_name="decision",
            name="target_weight_signed",
            field=models.DecimalField(decimal_places=4, default=0, max_digits=7),
        ),
    ]
