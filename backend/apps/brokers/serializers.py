"""REST serializers for the brokers app.

Credentials are NEVER returned through these — secret fields are masked
to a static "set" / "unset" sentinel so the frontend can render the
connection state without ever seeing plaintext.
"""
from __future__ import annotations

from rest_framework import serializers

from .credentials import has_credential
from .models import (
    BrokerAccount,
    BrokerFill,
    BrokerOrder,
    BrokerSyncEvent,
    DisclaimerAcceptance,
    LiveTradingDisclaimer,
)


class BrokerAccountSerializer(serializers.ModelSerializer):
    credential = serializers.SerializerMethodField()
    broker_display = serializers.SerializerMethodField()
    portfolio_id = serializers.IntegerField(source="portfolio.id", read_only=True)
    portfolio_name = serializers.CharField(source="portfolio.name", read_only=True)
    drift_pending = serializers.SerializerMethodField()

    class Meta:
        model = BrokerAccount
        fields = (
            "id", "broker", "broker_display", "mode", "account_id", "label",
            "base_currency", "default_quantity_mode",
            "connection_status", "is_active", "last_synced_at",
            "created_at", "portfolio_id", "portfolio_name", "credential",
            "drift_pending",
        )
        read_only_fields = (
            "id", "account_id", "connection_status", "is_active", "last_synced_at",
            "created_at", "portfolio_id", "portfolio_name", "broker_display",
            "credential", "drift_pending",
        )

    def get_credential(self, obj: BrokerAccount) -> dict:
        return {"status": "set" if has_credential(obj) else "unset"}

    def get_broker_display(self, obj: BrokerAccount) -> str:
        from .capabilities import get_capabilities
        cap = get_capabilities(obj.broker)
        return cap.display_name if cap else obj.broker

    def get_drift_pending(self, obj: BrokerAccount) -> bool:
        last = obj.last_drift_event_id
        if not last:
            return False
        ack = obj.drift_acknowledged_at
        if ack is None:
            return True
        # If a drift event has happened more recently than the ack, raise it.
        event = obj.last_drift_event
        if event is None:
            return False
        return bool(event.started_at and event.started_at > ack)


class BrokerOrderSerializer(serializers.ModelSerializer):
    decision_id = serializers.IntegerField(source="decision.id", read_only=True)
    notional_estimate = serializers.SerializerMethodField()

    class Meta:
        model = BrokerOrder
        fields = (
            "id", "broker_account", "client_order_id", "decision_id", "ticker",
            "side", "quantity", "order_type", "limit_price", "stop_price",
            "time_in_force", "status", "idempotency_state", "broker_order_id",
            "confirmed_at", "confirmation_method", "queued_until_open",
            "submitted_at", "filled_at", "cancelled_at", "avg_fill_price",
            "filled_quantity", "error_message", "group_id", "notional_estimate",
            "created_at",
        )
        read_only_fields = (
            "id", "client_order_id", "status", "idempotency_state",
            "broker_order_id", "confirmed_at", "confirmation_method",
            "queued_until_open", "submitted_at", "filled_at", "cancelled_at",
            "avg_fill_price", "filled_quantity", "error_message", "group_id",
            "created_at", "decision_id", "notional_estimate",
        )

    def get_notional_estimate(self, obj: BrokerOrder) -> str:
        from decimal import Decimal

        # Prefer the realised fill price, then the order's own reference
        # price (limit / stop). A market order with no fill yet has no
        # reference price — the UI estimates notional from the live quote.
        price = obj.avg_fill_price or obj.limit_price or obj.stop_price
        if price is None:
            return "0.00"
        return str((obj.quantity * price).quantize(Decimal("0.01")))


class BrokerFillSerializer(serializers.ModelSerializer):
    ticker = serializers.CharField(source="order.ticker", read_only=True)
    side = serializers.CharField(source="order.side", read_only=True)

    class Meta:
        model = BrokerFill
        fields = (
            "id", "order", "broker_fill_id", "ticker", "side", "quantity",
            "price", "filled_at", "created_at",
        )
        read_only_fields = fields


class BrokerSyncEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = BrokerSyncEvent
        fields = (
            "id", "broker_account", "triggered_by", "started_at", "finished_at",
            "drift_detected", "ledger_entries_written", "notes", "error_message",
        )
        read_only_fields = fields


class DisclaimerSerializer(serializers.ModelSerializer):
    accepted = serializers.SerializerMethodField()

    class Meta:
        model = LiveTradingDisclaimer
        fields = ("version", "body", "effective_from", "is_current", "accepted")
        read_only_fields = fields

    def get_accepted(self, obj: LiveTradingDisclaimer) -> bool:
        user = self.context.get("user")
        if user is None or not user.is_authenticated:
            return False
        return DisclaimerAcceptance.objects.filter(
            user=user, disclaimer=obj,
        ).exists()
