import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """P4 app-improvements: WS-B rerun-cycle (superseded_by + widened
    same-day unique to exclude failed) and WS-E enrollment (enrolled_at,
    enrollment_diff, auto_enroll_on_done, strategy-enroll ledger kinds).
    """

    dependencies = [
        ('portfolios', '0024_ledgerentry_broker_fks'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='portfoliotarget',
            name='uniq_active_strategy_target_per_day',
        ),
        migrations.AddField(
            model_name='portfoliostrategy',
            name='auto_enroll_on_done',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='portfoliotarget',
            name='enrolled_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='portfoliotarget',
            name='enrollment_diff',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='portfoliotarget',
            name='superseded_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='supersedes', to='portfolios.portfoliotarget',
            ),
        ),
        migrations.AlterField(
            model_name='ledgerentry',
            name='kind',
            field=models.CharField(
                choices=[
                    ('deposit', 'Cash deposit'),
                    ('withdrawal', 'Cash withdrawal'),
                    ('position_open', 'Position opened'),
                    ('position_increase', 'Position increased'),
                    ('position_reduce', 'Position reduced'),
                    ('position_close', 'Position closed'),
                    ('edit_adjustment', 'Manual edit adjustment'),
                    ('broker_fill', 'Broker fill'),
                    ('reconciliation_adjustment', 'Reconciliation adjustment'),
                    ('strategy_enroll', 'Strategy enrollment (open/increase)'),
                    ('strategy_enroll_reduce', 'Strategy enrollment (reduce)'),
                    ('strategy_enroll_close', 'Strategy enrollment (close)'),
                ],
                max_length=32,
            ),
        ),
        migrations.AddConstraint(
            model_name='portfoliotarget',
            constraint=models.UniqueConstraint(
                condition=models.Q(('status__in', ['cancelled', 'failed']), _negated=True),
                fields=('strategy', 'as_of_date'),
                name='uniq_active_strategy_target_per_day',
            ),
        ),
    ]
