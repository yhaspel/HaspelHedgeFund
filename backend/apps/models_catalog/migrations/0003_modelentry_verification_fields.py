from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("models_catalog", "0002_byok_data_providers"),
    ]
    operations = [
        migrations.AddField(
            model_name="modelentry",
            name="last_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="modelentry",
            name="last_verified_note",
            field=models.TextField(blank=True, default=""),
        ),
    ]
