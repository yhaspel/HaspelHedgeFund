"""P3a-1 broker models.

`BrokerAccount` is a per-user ForeignKey (NOT OneToOne) so a user can hold
multiple accounts — paper + live, IBKR + Alpaca, two demo books, etc. Each
account owns exactly one `kind="broker"` Portfolio (created with the
account, in one transaction; see `apps/brokers/credentials.py` and the
view layer).

Credentials live on a separate `BrokerCredential` row so OAuth tokens can
be rotated independently of the account itself, and so `disconnect` zeroes
the credential without dropping audit history on the account.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


def _broker_order_uuid() -> str:
    """Stable client_order_id — UUID4 stringified for both DB and broker side."""
    return str(uuid.uuid4())


class BrokerAccount(models.Model):
    MODE_PAPER = "paper"
    MODE_LIVE = "live"
    MODE_CHOICES = [(MODE_PAPER, "Paper"), (MODE_LIVE, "Live")]

    STATUS_CONNECTING = "connecting"
    STATUS_ACTIVE = "active"
    STATUS_NEEDS_REAUTH = "needs_reauth"
    STATUS_DISABLED = "disabled"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_CONNECTING, "Connecting"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_NEEDS_REAUTH, "Needs reauth"),
        (STATUS_DISABLED, "Disabled"),
        (STATUS_ERROR, "Error"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="broker_accounts",
        on_delete=models.CASCADE,
    )
    broker = models.CharField(max_length=32, db_index=True)
    mode = models.CharField(max_length=8, choices=MODE_CHOICES, default=MODE_PAPER)
    account_id = models.CharField(max_length=64)
    label = models.CharField(max_length=80)
    base_currency = models.CharField(max_length=8, default="USD")
    config = models.JSONField(default=dict, blank=True)
    portfolio = models.OneToOneField(
        "portfolios.Portfolio",
        on_delete=models.PROTECT,
        related_name="broker_account",
    )
    connection_status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_CONNECTING,
    )
    is_active = models.BooleanField(default=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_drift_event = models.ForeignKey(
        "brokers.BrokerSyncEvent",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    drift_acknowledged_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "broker", "account_id"],
                name="uniq_broker_account_per_user",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "broker"]),
        ]

    def __str__(self) -> str:
        return f"{self.broker}/{self.mode} u={self.user_id} {self.label}"


class BrokerCredential(models.Model):
    """One credential per account. Holds API-key OR OAuth material — never both.

    Encrypted values use `apps/brokers/credentials.py`, which derives a Fernet
    key from `settings.SECRET_KEY` (same pattern as `apps/models_catalog/crypto.py`
    from P2d). Plaintext is never returned through a serializer and never
    logged. P4a will migrate this to a per-tenant envelope vault.
    """

    AUTH_NONE = "none"
    AUTH_API_KEY = "api_key"
    AUTH_OAUTH2 = "oauth2"
    AUTH_OAUTH1 = "oauth1"
    AUTH_GATEWAY = "gateway_session"

    account = models.OneToOneField(
        BrokerAccount,
        on_delete=models.CASCADE,
        related_name="credential",
    )
    auth_kind = models.CharField(max_length=24, default=AUTH_NONE)
    encrypted_api_key = models.TextField(blank=True, default="")
    encrypted_api_secret = models.TextField(blank=True, default="")
    encrypted_access_token = models.TextField(blank=True, default="")
    encrypted_refresh_token = models.TextField(blank=True, default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)
    scopes = models.CharField(max_length=255, blank=True, default="")
    rotated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:  # pragma: no cover - human display only
        return f"cred a={self.account_id} kind={self.auth_kind}"

    def has_any_secret(self) -> bool:
        return bool(
            self.encrypted_api_key
            or self.encrypted_api_secret
            or self.encrypted_access_token
            or self.encrypted_refresh_token
        )


class BrokerOrder(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_CONFIRMED = "confirmed"
    STATUS_SUBMITTED = "submitted"
    STATUS_PARTIAL = "partial"
    STATUS_FILLED = "filled"
    STATUS_CANCELLED = "cancelled"
    STATUS_REJECTED = "rejected"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_CONFIRMED, "Confirmed"),
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_PARTIAL, "Partial fill"),
        (STATUS_FILLED, "Filled"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_ERROR, "Error"),
    ]
    OPEN_STATUSES = (STATUS_SUBMITTED, STATUS_PARTIAL)

    IDEM_UNSUBMITTED = "unsubmitted"
    IDEM_SUBMIT_PENDING = "submit_pending"
    IDEM_ACKNOWLEDGED = "acknowledged"
    IDEM_UNKNOWN = "unknown"
    IDEM_CHOICES = [
        (IDEM_UNSUBMITTED, "Unsubmitted"),
        (IDEM_SUBMIT_PENDING, "Submit pending"),
        (IDEM_ACKNOWLEDGED, "Acknowledged"),
        (IDEM_UNKNOWN, "Unknown"),
    ]

    CONFIRM_MANUAL = "manual_ui"
    CONFIRM_SCHEDULED = "scheduled_job"
    CONFIRM_API = "api"
    CONFIRM_CHOICES = [
        (CONFIRM_MANUAL, "Manual UI"),
        (CONFIRM_SCHEDULED, "Scheduled job"),
        (CONFIRM_API, "API"),
    ]

    broker_account = models.ForeignKey(
        BrokerAccount, on_delete=models.CASCADE, related_name="orders",
    )
    client_order_id = models.CharField(
        max_length=64, unique=True, editable=False, default=_broker_order_uuid,
    )
    decision = models.ForeignKey(
        "runs.Decision", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="broker_orders",
    )
    ticker = models.CharField(max_length=16, db_index=True)
    side = models.CharField(max_length=8)
    quantity = models.DecimalField(max_digits=20, decimal_places=8)
    order_type = models.CharField(max_length=12, default="market")
    limit_price = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True,
    )
    # Trigger price for stop orders. A stop order rests until the market
    # crosses this level, then fills at market. Null for market/limit orders.
    stop_price = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True,
    )
    time_in_force = models.CharField(max_length=8, default="day")
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT,
    )
    idempotency_state = models.CharField(
        max_length=16, choices=IDEM_CHOICES, default=IDEM_UNSUBMITTED,
    )
    broker_order_id = models.CharField(max_length=64, blank=True, default="")
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmation_method = models.CharField(
        max_length=16, choices=CONFIRM_CHOICES, blank=True, default="",
    )
    confirmation_ip = models.GenericIPAddressField(null=True, blank=True)
    confirmation_user_agent = models.CharField(max_length=255, blank=True, default="")
    confirmation_audit = models.JSONField(default=dict, blank=True)
    queued_until_open = models.BooleanField(default=False)
    submitted_at = models.DateTimeField(null=True, blank=True)
    filled_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    avg_fill_price = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True,
    )
    filled_quantity = models.DecimalField(
        max_digits=20, decimal_places=8, default=0,
    )
    error_message = models.TextField(blank=True, default="")
    raw_broker_response = models.JSONField(default=dict, blank=True)
    group_id = models.UUIDField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["broker_account", "status"]),
            models.Index(fields=["broker_account", "-created_at"]),
        ]
        constraints = [
            # An acknowledged order has a broker_order_id. Two orders on the
            # same account can never share the same broker id. (Partial:
            # null broker_order_id is unconstrained.)
            models.UniqueConstraint(
                fields=["broker_account", "broker_order_id"],
                condition=~models.Q(broker_order_id=""),
                name="uniq_broker_order_id_per_account",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.side} {self.ticker} q={self.quantity} "
            f"a={self.broker_account_id} {self.status}"
        )


class BrokerFill(models.Model):
    order = models.ForeignKey(
        BrokerOrder, on_delete=models.CASCADE, related_name="fills",
    )
    broker_fill_id = models.CharField(max_length=64)
    quantity = models.DecimalField(max_digits=20, decimal_places=8)
    price = models.DecimalField(max_digits=12, decimal_places=4)
    filled_at = models.DateTimeField()
    raw_broker_response = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["order", "broker_fill_id"],
                name="uniq_fill_per_order",
            ),
        ]
        indexes = [
            models.Index(fields=["order", "filled_at"]),
        ]

    def __str__(self) -> str:
        return f"fill o={self.order_id} {self.quantity}@{self.price}"


class BrokerSyncEvent(models.Model):
    TRIGGER_PERIODIC = "celery_periodic"
    TRIGGER_MANUAL = "manual"
    TRIGGER_POST_ORDER = "post_order"
    TRIGGER_CHOICES = [
        (TRIGGER_PERIODIC, "Celery periodic"),
        (TRIGGER_MANUAL, "Manual"),
        (TRIGGER_POST_ORDER, "Post-order"),
    ]

    broker_account = models.ForeignKey(
        BrokerAccount, on_delete=models.CASCADE, related_name="sync_events",
    )
    triggered_by = models.CharField(
        max_length=24, choices=TRIGGER_CHOICES, default=TRIGGER_PERIODIC,
    )
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    drift_detected = models.BooleanField(default=False)
    ledger_entries_written = models.IntegerField(default=0)
    notes = models.TextField(blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["broker_account", "-started_at"]),
        ]

    def __str__(self) -> str:
        return (
            f"sync a={self.broker_account_id} drift={self.drift_detected} "
            f"{self.started_at:%Y-%m-%d %H:%M}"
        )


class UserBrokerOAuthApp(models.Model):
    """Per-user OAuth developer-app credentials.

    A user supplies their own `client_id` / `client_secret` for the broker
    OAuth apps they register. The platform never ships shared creds; the
    BYOK precedence is: user row first, else Django setting fallback.
    P4a will migrate the encrypted fields into the per-tenant envelope
    vault alongside `BrokerCredential` and `ProviderKey`.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="broker_oauth_apps",
        on_delete=models.CASCADE,
    )
    broker = models.CharField(max_length=32, db_index=True)
    encrypted_client_id = models.TextField(blank=True, default="")
    encrypted_client_secret = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "broker"], name="uniq_user_broker_oauth_app",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"oauth-app u={self.user_id} b={self.broker}"

    def has_secret(self) -> bool:
        return bool(self.encrypted_client_id and self.encrypted_client_secret)


class LiveTradingDisclaimer(models.Model):
    version = models.CharField(max_length=16, unique=True)
    body = models.TextField()
    effective_from = models.DateTimeField()
    is_current = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_from"]

    def __str__(self) -> str:
        return f"disclaimer v={self.version} current={self.is_current}"


class DisclaimerAcceptance(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="disclaimer_acceptances",
        on_delete=models.CASCADE,
    )
    disclaimer = models.ForeignKey(
        LiveTradingDisclaimer, on_delete=models.PROTECT,
        related_name="acceptances",
    )
    accepted_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "disclaimer"],
                name="uniq_user_disclaimer_acceptance",
            ),
        ]
        indexes = [models.Index(fields=["user", "-accepted_at"])]

    def __str__(self) -> str:  # pragma: no cover
        return f"accept u={self.user_id} d={self.disclaimer_id}"
