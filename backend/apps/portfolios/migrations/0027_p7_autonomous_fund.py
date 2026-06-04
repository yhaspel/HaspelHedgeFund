# Phase 7 — autonomous fund: StrategyAutopilot, AutopilotRun, AutonomousFund,
# PortfolioStrategy.updated_at, and PortfolioTarget.AUTOPILOT_SUBMITTED status.
# (Pre-existing model/migration index/BigAutoField drift on this repo is left
# untouched — it is unrelated to Phase 7 and would otherwise pollute this diff.)
import django.db.models.deletion
from decimal import Decimal

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('brokers', '0006_p7_autonomous_fund'),
        ('notifications', '0003_notificationevent_ticker'),
        ('portfolios', '0026_portfoliotarget_council_alpha_baseline'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AutonomousFund',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(default='Autonomous Fund', max_length=80)),
                ('fund_dd_halt_pct', models.DecimalField(decimal_places=2, default=Decimal('6'), max_digits=5)),
                ('state', models.CharField(choices=[('active', 'Active'), ('halted', 'Halted')], default='active', max_length=16)),
                ('peak_equity_usd', models.DecimalField(blank=True, decimal_places=2, max_digits=16, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name='AutopilotRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fire_time_utc', models.DateTimeField()),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('running', 'Running'), ('submitted', 'Submitted'), ('skipped', 'Skipped'), ('halted', 'Halted'), ('failed', 'Failed')], default='pending', max_length=16)),
                ('submit_decision', models.JSONField(blank=True, default=dict)),
                ('guardrail_actions', models.JSONField(blank=True, default=dict)),
                ('error', models.TextField(blank=True, default='')),
                ('started_at', models.DateTimeField(auto_now_add=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'ordering': ['-started_at'],
            },
        ),
        migrations.CreateModel(
            name='StrategyAutopilot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_enabled', models.BooleanField(default=False)),
                ('cron_expression', models.CharField(default='30 16 * * 5', max_length=64)),
                ('timezone', models.CharField(default='America/New_York', max_length=64)),
                ('is_market_aware', models.BooleanField(default=True)),
                ('model_preset', models.CharField(default='frugal', max_length=32)),
                ('cost_ceiling_usd', models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True)),
                ('on_breach', models.CharField(choices=[('degrade', 'Degrade to a cheaper preset'), ('skip', 'Skip the cycle'), ('notify_only', 'Run anyway and notify')], default='degrade', max_length=16)),
                ('target_vol_pct', models.DecimalField(decimal_places=2, default=Decimal('10'), max_digits=5)),
                ('dd_soft_cut_pct', models.DecimalField(decimal_places=2, default=Decimal('5'), max_digits=5)),
                ('dd_hard_halt_pct', models.DecimalField(decimal_places=2, default=Decimal('7.5'), max_digits=5)),
                ('max_orders_per_day', models.PositiveIntegerField(default=30)),
                ('max_notional_per_day_usd', models.DecimalField(decimal_places=2, default=Decimal('50000'), max_digits=14)),
                ('liquidity_adv_cap_pct', models.DecimalField(decimal_places=2, default=Decimal('5'), max_digits=5)),
                ('short_mode', models.CharField(choices=[('single_name', 'Single-name shorts'), ('etf_hedge', 'ETF hedge'), ('cash', 'Cash (no shorts)')], default='single_name', max_length=16)),
                ('flatten_on_halt', models.BooleanField(default=False)),
                ('state', models.CharField(choices=[('active', 'Active'), ('soft_cut', 'Soft cut (gross halved)'), ('halted', 'Halted')], default='active', max_length=16)),
                ('peak_equity_usd', models.DecimalField(blank=True, decimal_places=2, max_digits=16, null=True)),
                ('last_run_at', models.DateTimeField(blank=True, null=True)),
                ('next_run_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.AddField(
            model_name='portfoliostrategy',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, null=True),
        ),
        migrations.AlterField(
            model_name='portfoliotarget',
            name='status',
            field=models.CharField(choices=[('queued', 'Queued'), ('screening', 'Screening'), ('awaiting_review', 'Awaiting review'), ('running_council', 'Running council'), ('constructing', 'Constructing'), ('running', 'Running'), ('done', 'Done'), ('failed', 'Failed'), ('cancelled', 'Cancelled'), ('autopilot_submitted', 'Autopilot submitted')], default='queued', max_length=20),
        ),
        migrations.AddField(
            model_name='autonomousfund',
            name='owner',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='funds', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='autonomousfund',
            name='strategies',
            field=models.ManyToManyField(blank=True, related_name='funds', to='portfolios.portfoliostrategy'),
        ),
        migrations.AddField(
            model_name='strategyautopilot',
            name='strategy',
            field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='autopilot', to='portfolios.portfoliostrategy'),
        ),
        migrations.AddField(
            model_name='strategyautopilot',
            name='broker_account',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='autopilots', to='brokers.brokeraccount'),
        ),
        migrations.AddField(
            model_name='strategyautopilot',
            name='notification_channel',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='autopilots', to='notifications.notificationchannel'),
        ),
        migrations.AddField(
            model_name='autopilotrun',
            name='autopilot',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='runs', to='portfolios.strategyautopilot'),
        ),
        migrations.AddField(
            model_name='autopilotrun',
            name='target',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='autopilot_runs', to='portfolios.portfoliotarget'),
        ),
        migrations.AddField(
            model_name='autopilotrun',
            name='broker_orders',
            field=models.ManyToManyField(blank=True, related_name='autopilot_runs', to='brokers.brokerorder'),
        ),
        migrations.AddConstraint(
            model_name='autopilotrun',
            constraint=models.UniqueConstraint(fields=('autopilot', 'fire_time_utc'), name='uniq_autopilot_fire_time'),
        ),
    ]
