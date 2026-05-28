"""Per-user TradeStation developer-app credentials (BYOK)."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient

from apps.brokers.adapters.tradestation_oauth import (
    resolve_app_credentials,
    set_user_app_credentials,
)
from apps.brokers.models import UserBrokerOAuthApp

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="ts-cred@example.com", password="x" * 12)


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


# --- resolver ---------------------------------------------------------------


@override_settings(TRADESTATION_CLIENT_ID="", TRADESTATION_CLIENT_SECRET="")
def test_resolver_returns_empty_when_nothing_configured(user):
    cid, csec, source = resolve_app_credentials(user)
    assert (cid, csec, source) == ("", "", "")


@override_settings(TRADESTATION_CLIENT_ID="env-id", TRADESTATION_CLIENT_SECRET="env-sec")
def test_resolver_falls_back_to_env(user):
    cid, csec, source = resolve_app_credentials(user)
    assert (cid, csec, source) == ("env-id", "env-sec", "env")


@override_settings(TRADESTATION_CLIENT_ID="env-id", TRADESTATION_CLIENT_SECRET="env-sec")
def test_resolver_user_row_overrides_env(user):
    set_user_app_credentials(user, client_id="user-id", client_secret="user-sec")
    cid, csec, source = resolve_app_credentials(user)
    assert (cid, csec, source) == ("user-id", "user-sec", "user")


def test_resolver_isolates_per_user(db):
    a = User.objects.create_user(email="a@example.com", password="x" * 12)
    b = User.objects.create_user(email="b@example.com", password="x" * 12)
    set_user_app_credentials(a, client_id="aid", client_secret="asec")
    cid_a, _, src_a = resolve_app_credentials(a)
    cid_b, _, src_b = resolve_app_credentials(b)
    assert (cid_a, src_a) == ("aid", "user")
    assert (cid_b, src_b) == ("", "")


# --- API --------------------------------------------------------------------


def test_get_credentials_reports_absent_initially(client):
    r = client.get("/api/broker-accounts/tradestation/app-credentials/")
    assert r.status_code == 200
    body = r.json()
    assert body["has_user_credentials"] is False
    assert body["client_id_masked"] == ""


def test_put_credentials_persists_and_masks(client, user):
    r = client.put(
        "/api/broker-accounts/tradestation/app-credentials/",
        {"client_id": "abcd1234efgh", "client_secret": "secretvalue"},
        format="json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["has_user_credentials"] is True
    assert body["source"] == "user"
    assert body["client_id_masked"].endswith("efgh")
    assert "abcd" not in body["client_id_masked"]
    row = UserBrokerOAuthApp.objects.get(user=user, broker="tradestation")
    assert row.has_secret()


def test_put_clears_when_both_empty(client, user):
    set_user_app_credentials(user, client_id="x", client_secret="y")
    r = client.put(
        "/api/broker-accounts/tradestation/app-credentials/",
        {"client_id": "", "client_secret": ""},
        format="json",
    )
    assert r.status_code == 200
    assert r.json()["has_user_credentials"] is False


def test_put_rejects_partial(client):
    r = client.put(
        "/api/broker-accounts/tradestation/app-credentials/",
        {"client_id": "only-id", "client_secret": ""},
        format="json",
    )
    assert r.status_code == 400


@override_settings(TRADESTATION_CLIENT_ID="", TRADESTATION_CLIENT_SECRET="")
def test_runtime_config_flips_after_user_creds(client, user):
    r = client.get("/api/broker-accounts/tradestation/runtime-config/")
    assert r.status_code == 200
    assert r.json()["configured"] is False
    set_user_app_credentials(user, client_id="abc", client_secret="def")
    r = client.get("/api/broker-accounts/tradestation/runtime-config/")
    assert r.json()["configured"] is True
    assert r.json()["source"] == "user"
