from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

import apps.brokers.models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("portfolios", "0023_broker_portfolio_kind"),
        ("runs", "0007_run_investor_profile_applied"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="LiveTradingDisclaimer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("version", models.CharField(max_length=16, unique=True)),
                ("body", models.TextField()),
                ("effective_from", models.DateTimeField()),
                ("is_current", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-effective_from"]},
        ),
        migrations.CreateModel(
            name="BrokerAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("broker", models.CharField(db_index=True, max_length=32)),
                ("mode", models.CharField(choices=[("paper", "Paper"), ("live", "Live")], default="paper", max_length=8)),
                ("account_id", models.CharField(max_length=64)),
                ("label", models.CharField(max_length=80)),
                ("base_currency", models.CharField(default="USD", max_length=8)),
                ("config", models.JSONField(blank=True, default=dict)),
                ("connection_status", models.CharField(
                    choices=[("connecting", "Connecting"), ("active", "Active"),
                             ("needs_reauth", "Needs reauth"), ("disabled", "Disabled"),
                             ("error", "Error")],
                    default="connecting", max_length=16,
                )),
                ("is_active", models.BooleanField(default=True)),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                ("drift_acknowledged_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("portfolio", models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="broker_account",
                    to="portfolios.portfolio",
                )),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="broker_accounts",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
        ),
        migrations.AddConstraint(
            model_name="brokeraccount",
            constraint=models.UniqueConstraint(
                fields=("user", "broker", "account_id"),
                name="uniq_broker_account_per_user",
            ),
        ),
        migrations.AddIndex(
            model_name="brokeraccount",
            index=models.Index(fields=["user", "broker"], name="brokers_bro_user_id_1a8c28_idx"),
        ),
        migrations.CreateModel(
            name="BrokerSyncEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("triggered_by", models.CharField(
                    choices=[("celery_periodic", "Celery periodic"), ("manual", "Manual"),
                             ("post_order", "Post-order")],
                    default="celery_periodic", max_length=24,
                )),
                ("started_at", models.DateTimeField()),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("drift_detected", models.BooleanField(default=False)),
                ("ledger_entries_written", models.IntegerField(default=0)),
                ("notes", models.TextField(blank=True, default="")),
                ("error_message", models.TextField(blank=True, default="")),
                ("broker_account", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="sync_events",
                    to="brokers.brokeraccount",
                )),
            ],
            options={"ordering": ["-started_at"]},
        ),
        migrations.AddIndex(
            model_name="brokersyncevent",
            index=models.Index(
                fields=["broker_account", "-started_at"],
                name="brokers_bro_broker__3c0c9c_idx",
            ),
        ),
        migrations.AddField(
            model_name="brokeraccount",
            name="last_drift_event",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="brokers.brokersyncevent",
            ),
        ),
        migrations.CreateModel(
            name="BrokerCredential",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("auth_kind", models.CharField(default="none", max_length=24)),
                ("encrypted_api_key", models.TextField(blank=True, default="")),
                ("encrypted_api_secret", models.TextField(blank=True, default="")),
                ("encrypted_access_token", models.TextField(blank=True, default="")),
                ("encrypted_refresh_token", models.TextField(blank=True, default="")),
                ("token_expires_at", models.DateTimeField(blank=True, null=True)),
                ("scopes", models.CharField(blank=True, default="", max_length=255)),
                ("rotated_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("account", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="credential",
                    to="brokers.brokeraccount",
                )),
            ],
        ),
        migrations.CreateModel(
            name="BrokerOrder",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("client_order_id", models.CharField(
                    default=apps.brokers.models._broker_order_uuid,
                    editable=False, max_length=64, unique=True,
                )),
                ("ticker", models.CharField(db_index=True, max_length=16)),
                ("side", models.CharField(max_length=8)),
                ("quantity", models.DecimalField(decimal_places=8, max_digits=20)),
                ("order_type", models.CharField(default="market", max_length=12)),
                ("limit_price", models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True)),
                ("time_in_force", models.CharField(default="day", max_length=8)),
                ("status", models.CharField(
                    choices=[("draft", "Draft"), ("confirmed", "Confirmed"),
                             ("submitted", "Submitted"), ("partial", "Partial fill"),
                             ("filled", "Filled"), ("cancelled", "Cancelled"),
                             ("rejected", "Rejected"), ("error", "Error")],
                    default="draft", max_length=16,
                )),
                ("idempotency_state", models.CharField(
                    choices=[("unsubmitted", "Unsubmitted"),
                             ("submit_pending", "Submit pending"),
                             ("acknowledged", "Acknowledged"),
                             ("unknown", "Unknown")],
                    default="unsubmitted", max_length=16,
                )),
                ("broker_order_id", models.CharField(blank=True, default="", max_length=64)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("confirmation_method", models.CharField(
                    blank=True, default="",
                    choices=[("manual_ui", "Manual UI"),
                             ("scheduled_job", "Scheduled job"),
                             ("api", "API")],
                    max_length=16,
                )),
                ("confirmation_ip", models.GenericIPAddressField(blank=True, null=True)),
                ("confirmation_user_agent", models.CharField(blank=True, default="", max_length=255)),
                ("confirmation_audit", models.JSONField(blank=True, default=dict)),
                ("queued_until_open", models.BooleanField(default=False)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("filled_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("avg_fill_price", models.DecimalField(
                    blank=True, decimal_places=4, max_digits=12, null=True,
                )),
                ("filled_quantity", models.DecimalField(
                    decimal_places=8, default=0, max_digits=20,
                )),
                ("error_message", models.TextField(blank=True, default="")),
                ("raw_broker_response", models.JSONField(blank=True, default=dict)),
                ("group_id", models.UUIDField(blank=True, db_index=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("broker_account", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="orders",
                    to="brokers.brokeraccount",
                )),
                ("confirmed_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("decision", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="broker_orders",
                    to="runs.decision",
                )),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="brokerorder",
            index=models.Index(
                fields=["broker_account", "status"],
                name="brokers_bro_broker__b479aa_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="brokerorder",
            index=models.Index(
                fields=["broker_account", "-created_at"],
                name="brokers_bro_broker__078229_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="brokerorder",
            constraint=models.UniqueConstraint(
                condition=models.Q(("broker_order_id", ""), _negated=True),
                fields=("broker_account", "broker_order_id"),
                name="uniq_broker_order_id_per_account",
            ),
        ),
        migrations.CreateModel(
            name="BrokerFill",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("broker_fill_id", models.CharField(max_length=64)),
                ("quantity", models.DecimalField(decimal_places=8, max_digits=20)),
                ("price", models.DecimalField(decimal_places=4, max_digits=12)),
                ("filled_at", models.DateTimeField()),
                ("raw_broker_response", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("order", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="fills",
                    to="brokers.brokerorder",
                )),
            ],
        ),
        migrations.AddConstraint(
            model_name="brokerfill",
            constraint=models.UniqueConstraint(
                fields=("order", "broker_fill_id"),
                name="uniq_fill_per_order",
            ),
        ),
        migrations.AddIndex(
            model_name="brokerfill",
            index=models.Index(
                fields=["order", "filled_at"],
                name="brokers_bro_order_i_c4078c_idx",
            ),
        ),
        migrations.CreateModel(
            name="DisclaimerAcceptance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("accepted_at", models.DateTimeField(auto_now_add=True)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.CharField(blank=True, default="", max_length=255)),
                ("disclaimer", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="acceptances",
                    to="brokers.livetradingdisclaimer",
                )),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="disclaimer_acceptances",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
        ),
        migrations.AddConstraint(
            model_name="disclaimeracceptance",
            constraint=models.UniqueConstraint(
                fields=("user", "disclaimer"),
                name="uniq_user_disclaimer_acceptance",
            ),
        ),
        migrations.AddIndex(
            model_name="disclaimeracceptance",
            index=models.Index(
                fields=["user", "-accepted_at"],
                name="brokers_dis_user_id_aaa7d8_idx",
            ),
        ),
    ]
