from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('portfolios', '0013_regime_break'),
    ]

    operations = [
        migrations.CreateModel(
            name='PairZHistory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('as_of_date', models.DateField(db_index=True)),
                ('z', models.FloatField()),
                ('spread', models.FloatField()),
                ('leg_a_close', models.DecimalField(
                    max_digits=14, decimal_places=4, null=True, blank=True)),
                ('leg_b_close', models.DecimalField(
                    max_digits=14, decimal_places=4, null=True, blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('pair', models.ForeignKey(
                    to='portfolios.pair',
                    related_name='z_history',
                    on_delete=models.deletion.CASCADE,
                )),
            ],
            options={
                'unique_together': {('pair', 'as_of_date')},
                'indexes': [models.Index(fields=['pair', 'as_of_date'],
                                          name='portfolios__pair_id_b5d3c7_idx')],
            },
        ),
    ]
