"""Wave 3 / WP P3 — the ``ENABLED_BROKERS`` deployment gate.

IBKR (P3a-2) and TradeStation (P3a-3) are deferred phases whose adapters and
connect-wizard tiles shipped early, implying a capability that is not there.
The registry now marks them ``enabled: false`` / ``status: "deferred"`` with a
note, and ``POST /api/broker-accounts/`` refuses them with 400 + that note.
Nothing is deleted — one env var re-enables the whole path.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient

from apps.brokers import capabilities as caps
from apps.brokers.models import BrokerAccount
from apps.portfolios.models import Portfolio

User = get_user_model()

DEFERRED = ("ibkr", "tradestation")


@pytest.fixture
def user(db):
    return User.objects.create_user(email="w3p3-brokers@x.test", password="pw-fake-123456789")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _by_code(payload):
    return {row["code"]: row for row in payload["brokers"]}


# ---------------------------------------------------------------------------
# Registry payload
# ---------------------------------------------------------------------------
def test_registry_marks_ibkr_and_tradestation_deferred_with_a_note(client):
    rows = _by_code(client.get("/api/brokers/").json())
    for code in DEFERRED:
        row = rows[code]
        assert row["enabled"] is False
        assert row["status"] == "deferred"
        assert row["note"], "a disabled broker must say why"
        assert "deferred phase" in row["note"]
    # The default roster stays connectable.
    assert rows["alpaca_paper"]["enabled"] is True
    assert rows["alpaca_paper"]["status"] == "enabled"
    assert rows["alpaca_paper"]["note"] == ""
    assert rows["mock"]["enabled"] is True


def test_registry_keeps_every_pre_existing_capability_key(client):
    row = _by_code(client.get("/api/brokers/").json())["ibkr"]
    # Additive only — the wizard's existing fields are untouched.
    assert set(row) >= {
        "code", "display_name", "auth_kind", "supports_paper", "supports_live",
        "supports_fractional", "quantity_increment", "supported_order_types",
        "supported_time_in_force", "supports_bracket", "description", "available",
        "community_unverified", "connect_form",
    }
    assert row["available"] is True, "the adapter still exists; only the gate is off"


def test_enabled_brokers_lead_the_list(client):
    rows = client.get("/api/brokers/").json()["brokers"]
    enabled_flags = [r["enabled"] for r in rows]
    assert enabled_flags == sorted(enabled_flags, reverse=True)


@override_settings(ENABLED_BROKERS=["alpaca_paper", "mock", "ibkr", "tradestation"])
def test_one_setting_re_enables_the_deferred_brokers(client):
    rows = _by_code(client.get("/api/brokers/").json())
    for code in DEFERRED:
        assert rows[code]["enabled"] is True
        assert rows[code]["status"] == "enabled"
        assert rows[code]["note"] == ""


@override_settings(ENABLED_BROKERS=["alpaca_paper"])
def test_a_broker_switched_off_but_not_deferred_reports_disabled(client):
    row = _by_code(client.get("/api/brokers/").json())["mock"]
    assert row["enabled"] is False
    assert row["status"] == "disabled"
    assert "ENABLED_BROKERS" in row["note"]


# ---------------------------------------------------------------------------
# Setting semantics
# ---------------------------------------------------------------------------
def test_demo_is_an_accepted_alias_for_the_registered_mock_code(db):
    with override_settings(ENABLED_BROKERS=["alpaca_paper", "demo"]):
        assert caps.enabled_broker_codes() == frozenset({"alpaca_paper", "mock"})
        assert caps.is_broker_enabled("mock") is True
        assert caps.is_broker_enabled("demo") is True
        assert caps.is_broker_enabled("ibkr") is False


def test_a_comma_separated_env_string_is_accepted(db):
    with override_settings(ENABLED_BROKERS="alpaca_paper, ibkr"):
        assert caps.enabled_broker_codes() == frozenset({"alpaca_paper", "ibkr"})


def test_an_empty_setting_falls_back_to_the_default_rather_than_disabling_everything(db):
    with override_settings(ENABLED_BROKERS=[]):
        assert caps.enabled_broker_codes() == frozenset(caps.DEFAULT_ENABLED_BROKERS)


def test_default_when_the_setting_is_absent(db, settings):
    if hasattr(settings, "ENABLED_BROKERS"):
        del settings.ENABLED_BROKERS
    assert caps.enabled_broker_codes() == frozenset({"alpaca_paper", "mock"})
    for code in DEFERRED:
        assert caps.is_broker_enabled(code) is False


# ---------------------------------------------------------------------------
# The create entry point
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code", DEFERRED)
def test_creating_an_account_for_a_deferred_broker_is_400_with_the_note(client, code):
    resp = client.post(
        "/api/broker-accounts/", {"broker": code, "mode": "paper", "label": "x"},
        format="json",
    )
    assert resp.status_code == 400, resp.content
    detail = resp.json()["broker"]
    message = detail if isinstance(detail, str) else detail[0]
    assert "deferred phase" in message
    assert message == caps.disabled_reason(code)
    assert not BrokerAccount.objects.filter(broker=code).exists()


def test_creating_an_account_for_an_enabled_broker_still_works(client):
    resp = client.post(
        "/api/broker-accounts/", {"broker": "mock", "mode": "paper", "label": "Demo"},
        format="json",
    )
    assert resp.status_code == 201, resp.content
    assert BrokerAccount.objects.filter(broker="mock").exists()


@override_settings(ENABLED_BROKERS=["alpaca_paper", "mock", "tradestation"])
def test_flipping_the_setting_makes_a_deferred_broker_creatable_again(client):
    resp = client.post(
        "/api/broker-accounts/", {"broker": "tradestation", "mode": "paper", "label": "TS"},
        format="json",
    )
    assert resp.status_code == 201, resp.content


def test_an_existing_account_on_a_deferred_broker_is_not_orphaned(client, user):
    """The gate is on the CREATE entry point only — a connected book keeps
    working, so switching a broker off never strands a live account."""
    pf = Portfolio.objects.create(
        user=user, kind=Portfolio.KIND_BROKER, name="bk-ib", cash_balance=Decimal("1000"),
    )
    acc = BrokerAccount.objects.create(
        user=user, broker="ibkr", mode=BrokerAccount.MODE_PAPER, account_id="DU1",
        label="IB", portfolio=pf, connection_status=BrokerAccount.STATUS_ACTIVE,
    )
    listed = client.get("/api/broker-accounts/").json()
    rows = listed["results"] if isinstance(listed, dict) else listed
    assert any(r["id"] == acc.id for r in rows)


def test_the_deferred_adapters_are_still_registered(db):
    """"Do not delete the code" — the adapters remain importable and in the
    registry, so re-enabling is a config change, not a code change."""
    for code in DEFERRED:
        assert caps.get_capabilities(code) is not None
        assert caps.get_adapter_factory(code) is not None
