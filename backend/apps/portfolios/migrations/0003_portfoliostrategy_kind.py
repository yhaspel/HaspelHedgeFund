from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('portfolios', '0002_rename_portfolios__strateg_3a1f7c_idx_portfolios__strateg_2f6e5c_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='portfoliostrategy',
            name='kind',
            field=models.CharField(
                choices=[
                    ('long_only', 'Long-only'),
                    ('short_only', 'Short-only'),
                    ('long_short', 'Long/Short'),
                    ('market_neutral', 'Market-neutral'),
                ],
                default='long_short',
                max_length=16,
            ),
        ),
    ]
