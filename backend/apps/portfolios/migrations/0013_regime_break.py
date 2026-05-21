from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('portfolios', '0012_pair_council'),
    ]

    operations = [
        migrations.AddField(
            model_name='pair', name='consecutive_coint_failures',
            field=models.SmallIntegerField(default=0),
        ),
    ]
