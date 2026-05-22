# P3 prereq 2 (WS-2): public reference data for ticker identity.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('data', '0004_p2m_markov_regime'),
    ]

    operations = [
        migrations.CreateModel(
            name='CompanyProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ticker', models.CharField(db_index=True, max_length=16, unique=True)),
                ('name', models.CharField(blank=True, default='', max_length=256)),
                ('exchange', models.CharField(blank=True, default='', max_length=32)),
                ('sector', models.CharField(blank=True, default='', max_length=64)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
