from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('portfolios', '0008_sector_council_v2'),
    ]

    operations = [
        migrations.AlterField(
            model_name='portfoliostrategy',
            name='kind',
            field=models.CharField(
                choices=[
                    ('long_only', 'Long-only'),
                    ('short_only', 'Short-only'),
                    ('long_short', 'Long/Short'),
                    ('market_neutral', 'Market-neutral'),
                    ('concentrated_long', 'Concentrated long-only'),
                    ('sector_rotation', 'Sector / thematic ETF rotation'),
                    ('global_macro', 'Global macro (ETF expression)'),
                ],
                default='long_short',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='portfoliostrategy',
            name='asset_class_caps',
            field=models.JSONField(default=dict, blank=True),
        ),
        migrations.AddField(
            model_name='portfoliostrategy',
            name='prefer_inverse_etf_over_short',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='portfoliostrategy',
            name='max_inverse_etf_hold_days',
            field=models.SmallIntegerField(default=14),
        ),
        migrations.CreateModel(
            name='MacroETF',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('ticker', models.CharField(max_length=16, unique=True)),
                ('asset_class', models.CharField(max_length=16, choices=[
                    ('equity', 'Equity'), ('rates', 'Rates'),
                    ('inflation', 'Inflation'), ('commodity', 'Commodity'),
                    ('fx_proxy', 'FX proxy'), ('em', 'Emerging markets'),
                ])),
                ('direction', models.CharField(max_length=8, default='long')),
                ('inverse_of', models.CharField(max_length=16, blank=True, default='')),
                ('duration_years', models.FloatField(null=True, blank=True)),
                ('issuer', models.CharField(max_length=32, default='')),
                ('expense_ratio_bps', models.SmallIntegerField(default=10)),
                ('is_active', models.BooleanField(default=True)),
                ('regime_affinities', models.JSONField(default=dict, blank=True)),
                ('description', models.TextField(blank=True, default='')),
                ('tracking_note', models.TextField(blank=True, default='')),
            ],
        ),
        migrations.CreateModel(
            name='MacroRegimeSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('as_of_date', models.DateField(db_index=True)),
                ('growth_score', models.FloatField(default=0.0)),
                ('inflation_score', models.FloatField(default=0.0)),
                ('policy_stance', models.CharField(max_length=16, default='neutral')),
                ('yield_curve_state', models.CharField(max_length=16, default='flat')),
                ('risk_on_score', models.FloatField(default=0.0)),
                ('regime_vector', models.JSONField(default=dict, blank=True)),
                ('source_macro_snapshot_id', models.IntegerField(null=True, blank=True)),
                ('raw', models.JSONField(default=dict, blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('strategy', models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name='macro_regime_snapshots',
                    to='portfolios.portfoliostrategy',
                )),
            ],
            options={
                'unique_together': {('strategy', 'as_of_date')},
            },
        ),
    ]
