from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("models_catalog", "0004_providerkey_resend_api_key_enc_alter_providerkey_id_and_more"),
    ]
    operations = [
        migrations.AddField(
            model_name="modelentry",
            name="supports_reasoning",
            field=models.BooleanField(default=False),
        ),
    ]
