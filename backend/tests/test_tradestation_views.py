"""TradeStation connect-flow view-layer tests (P3a-3)."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.brokers.adapters import tradestation_oauth as oauth
from apps.brokers.credentials import set_oauth_tokens
from apps.brokers.models import BrokerAccount, BrokerCredential

User = get_user_model()

SIM_BASE = "https://sim-api.tradestation.com/v3"


# Wave 3 (WP P3): ``ENABLED_BROKERS`` gates which brokers can back a NEW
# account, and IBKR / TradeStation ship deferred. These tests exercise those
# adapters deliberately, so the module switches them on; the gate itself is
# covered by tests/test_wave3_p3_broker_gate.py.
@pytest.fixture(autouse=True)
def _enable_deferred_brokers(settings):
    settings.ENABLED_BROKERS = ["alpaca_paper", "mock", "ibkr", "tradestation"]


@pytest.fixture
def user(db):
    return User.objects.create_user(email="ts-view@example.com", password="x" * 12)


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@override_settings(
    TRADESTATION_CLIENT_ID="cid",
    TRADESTATION_CLIENT_SECRET="csec",
    TRADESTATION_API_BASE_SIM=SIM_BASE,
)
def test_oauth_start_returns_authorize_url_and_stashes_verifier(
    user, client, db,
):
    r = client.post("/api/broker-accounts/", {
        "broker": "tradestation", "mode": "paper",
        "label": "TS test",
    }, format="json")
    assert r.status_code == 201, r.content
    account_id = r.json()["id"]

    r = client.post(
        f"/api/broker-accounts/{account_id}/oauth/start/", {}, format="json",
    )
    assert r.status_code == 200, r.content
    assert "authorization_url" in r.json()
    acc = BrokerAccount.objects.get(pk=account_id)
    assert acc.config.get("pkce_verifier")
    assert acc.config.get("oauth_state") == r.json()["state"]
    assert acc.config.get("api_base_url") == SIM_BASE


@override_settings(TRADESTATION_CLIENT_ID="")
def test_oauth_start_blocks_when_unconfigured(client):
    r = client.post("/api/broker-accounts/", {
        "broker": "tradestation", "mode": "paper", "label": "TS",
    }, format="json")
    aid = r.json()["id"]
    r = client.post(
        f"/api/broker-accounts/{aid}/oauth/start/", {}, format="json",
    )
    assert r.status_code == 400
    assert "not configured" in str(r.content)


@override_settings(
    TRADESTATION_CLIENT_ID="cid", TRADESTATION_CLIENT_SECRET="csec",
    TRADESTATION_API_BASE_SIM=SIM_BASE,
)
def test_oauth_callback_completes_handshake(monkeypatch, user, client):
    r = client.post("/api/broker-accounts/", {
        "broker": "tradestation", "mode": "paper", "label": "TS",
    }, format="json")
    aid = r.json()["id"]
    r = client.post(
        f"/api/broker-accounts/{aid}/oauth/start/", {}, format="json",
    )
    state = r.json()["state"]

    def fake_post(url, data):
        assert data["grant_type"] == "authorization_code"
        return {
            "access_token": "atok",
            "refresh_token": "rtok",
            "expires_in": 1200,
            "scope": "openid offline_access ReadAccount Trade",
        }

    monkeypatch.setattr(oauth, "_post", fake_post)

    # Callback is unauthenticated; use a fresh client.
    public = APIClient()
    r = public.get(
        "/api/broker-accounts/oauth/callback/",
        {"code": "thecode", "state": state},
    )
    assert r.status_code == 200, r.content
    acc = BrokerAccount.objects.get(pk=aid)
    assert acc.credential.auth_kind == BrokerCredential.AUTH_OAUTH2
    assert acc.credential.token_expires_at > timezone.now()
    assert "pkce_verifier" not in (acc.config or {})


def test_oauth_callback_rejects_bad_state(db):
    public = APIClient()
    r = public.get(
        "/api/broker-accounts/oauth/callback/",
        {"code": "x", "state": "tampered"},
    )
    assert r.status_code == 400
    assert "invalid or expired" in str(r.content).lower()


@override_settings(
    TRADESTATION_CLIENT_ID="cid", TRADESTATION_CLIENT_SECRET="csec",
    TRADESTATION_API_BASE_SIM=SIM_BASE,
)
def test_activate_flips_status_active_and_records_id(user, client, monkeypatch):
    r = client.post("/api/broker-accounts/", {
        "broker": "tradestation", "mode": "paper", "label": "TS",
    }, format="json")
    aid = r.json()["id"]
    acc = BrokerAccount.objects.get(pk=aid)
    acc.config = {"api_base_url": SIM_BASE, "oauth_state": "s"}
    acc.save(update_fields=["config"])
    set_oauth_tokens(
        acc, access_token="a", refresh_token="r",
        expires_at=timezone.now() + timedelta(hours=1),
        flavor=BrokerCredential.AUTH_OAUTH2,
    )
    r = client.post(
        f"/api/broker-accounts/{aid}/tradestation/activate/",
        {"account_id": "SIMABC"}, format="json",
    )
    assert r.status_code == 200, r.content
    acc.refresh_from_db()
    assert acc.account_id == "SIMABC"
    assert acc.connection_status == BrokerAccount.STATUS_ACTIVE
    assert "oauth_state" not in (acc.config or {})


def test_runtime_config_reports_configuration(client):
    with override_settings(TRADESTATION_CLIENT_ID="", TRADESTATION_CLIENT_SECRET=""):
        r = client.get("/api/broker-accounts/tradestation/runtime-config/")
        assert r.status_code == 200
        assert r.json()["configured"] is False
    with override_settings(TRADESTATION_CLIENT_ID="x", TRADESTATION_CLIENT_SECRET="y"):
        r = client.get("/api/broker-accounts/tradestation/runtime-config/")
        assert r.json()["configured"] is True
