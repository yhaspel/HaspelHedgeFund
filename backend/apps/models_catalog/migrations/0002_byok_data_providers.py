from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("models_catalog", "0001_initial"),
    ]
    operations = [
        migrations.AddField(
            model_name="providerkey",
            name="fmp_api_key_enc",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="providerkey",
            name="tiingo_api_key_enc",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="providerkey",
            name="fred_api_key_enc",
            field=models.TextField(blank=True, default=""),
        ),
    ]
