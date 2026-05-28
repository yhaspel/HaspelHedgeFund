"""TradeStation OAuth handshake unit tests (P3a-3, ADR 0012).

The HTTP boundary (`_post`) is patched per test; no live network.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone

from apps.brokers.adapters import tradestation_oauth as oauth
from apps.brokers.credentials import set_oauth_tokens
from apps.brokers.interfaces import BrokerError
from apps.brokers.models import BrokerAccount, BrokerCredential
from apps.portfolios.models import Portfolio

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="ts-oauth@example.com", password="x" * 12)


@pytest.fixture
def account(user) -> BrokerAccount:
    p = Portfolio.objects.create(
        user=user, name="Broker · TS paper", kind=Portfolio.KIND_BROKER,
        cash_balance=Decimal("0"),
    )
    return BrokerAccount.objects.create(
        user=user, broker="tradestation", mode="paper",
        account_id="pending-x", label="TS paper test", portfolio=p,
        connection_status=BrokerAccount.STATUS_CONNECTING,
    )


# --- PKCE -------------------------------------------------------------------


def test_make_pkce_pair_produces_valid_pair():
    v, c = oauth.make_pkce_pair()
    assert 43 <= len(v) <= 128
    assert len(c) == 43  # 256/6 → 43 base64url chars (unpadded)
    assert c != v


# --- state ------------------------------------------------------------------


def test_state_round_trip_then_tamper_fails():
    state = oauth.sign_state(123, 456)
    acc_id, user_id = oauth.verify_state(state)
    assert (acc_id, user_id) == (123, 456)
    with pytest.raises(BrokerError):
        oauth.verify_state(state + "x")


# --- authorize URL ----------------------------------------------------------


@override_settings(
    TRADESTATION_CLIENT_ID="client123",
    TRADESTATION_CLIENT_SECRET="secret123",
    TRADESTATION_REDIRECT_URI="http://localhost/callback",
    TRADESTATION_AUTHORIZE_URL="https://signin.tradestation.com/authorize",
    TRADESTATION_SCOPES="openid offline_access ReadAccount Trade",
)
def test_build_authorize_url_carries_scopes_state_pkce(account):
    req = oauth.build_authorize_url(account=account)
    qs = parse_qs(urlparse(req.url).query)
    assert qs["client_id"] == ["client123"]
    assert qs["redirect_uri"] == ["http://localhost/callback"]
    assert qs["scope"] == ["openid offline_access ReadAccount Trade"]
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["state"][0] == req.state
    assert qs["code_challenge"][0]
    # State binds the account.
    acc_id, _ = oauth.verify_state(qs["state"][0])
    assert acc_id == account.pk


@override_settings(TRADESTATION_CLIENT_ID="")
def test_build_authorize_url_blocks_when_unconfigured(account):
    with pytest.raises(BrokerError, match="not configured"):
        oauth.build_authorize_url(account=account)


# --- token exchange ---------------------------------------------------------


@override_settings(
    TRADESTATION_CLIENT_ID="cid", TRADESTATION_CLIENT_SECRET="csecret",
)
def test_exchange_code_for_tokens(monkeypatch):
    captured = {}

    def fake_post(url, data):
        captured["url"] = url
        captured["data"] = data
        return {
            "access_token": "atok",
            "refresh_token": "rtok",
            "expires_in": 1200,
            "scope": "openid offline_access ReadAccount Trade",
        }

    monkeypatch.setattr(oauth, "_post", fake_post)
    bundle = oauth.exchange_code_for_tokens(
        code="thecode", code_verifier="theverifier", user=None,
    )
    assert bundle.access_token == "atok"
    assert bundle.refresh_token == "rtok"
    assert bundle.expires_in == 1200
    assert captured["data"]["grant_type"] == "authorization_code"
    assert captured["data"]["code"] == "thecode"
    assert captured["data"]["code_verifier"] == "theverifier"


# --- refresh ----------------------------------------------------------------


@override_settings(
    TRADESTATION_CLIENT_ID="cid", TRADESTATION_CLIENT_SECRET="csecret",
)
def test_refresh_if_needed_skips_when_fresh(monkeypatch, account):
    now = timezone.now()
    set_oauth_tokens(
        account, access_token="still-good", refresh_token="r",
        expires_at=now + timedelta(hours=1),
        flavor=BrokerCredential.AUTH_OAUTH2,
    )

    def fail(*a, **kw):
        raise AssertionError("should not refresh")

    monkeypatch.setattr(oauth, "_post", fail)
    token = oauth.refresh_if_needed(account)
    assert token == "still-good"


@override_settings(
    TRADESTATION_CLIENT_ID="cid", TRADESTATION_CLIENT_SECRET="csecret",
)
def test_refresh_if_needed_rotates_near_expiry(monkeypatch, account):
    set_oauth_tokens(
        account, access_token="old", refresh_token="r0",
        expires_at=timezone.now() + timedelta(seconds=10),
        flavor=BrokerCredential.AUTH_OAUTH2,
    )

    def fake_post(url, data):
        assert data["grant_type"] == "refresh_token"
        return {
            "access_token": "new",
            "refresh_token": "r1",  # rotated
            "expires_in": 1200,
            "scope": "openid offline_access ReadAccount Trade",
        }

    monkeypatch.setattr(oauth, "_post", fake_post)
    token = oauth.refresh_if_needed(account)
    assert token == "new"
    account.refresh_from_db()
    assert account.credential.token_expires_at > timezone.now() + timedelta(minutes=5)


@override_settings(
    TRADESTATION_CLIENT_ID="cid", TRADESTATION_CLIENT_SECRET="csecret",
)
def test_refresh_failure_flips_to_needs_reauth(monkeypatch, account):
    set_oauth_tokens(
        account, access_token="old", refresh_token="dead",
        expires_at=timezone.now() - timedelta(minutes=1),
        flavor=BrokerCredential.AUTH_OAUTH2,
    )

    def reject(url, data):
        raise BrokerError("invalid_grant")

    monkeypatch.setattr(oauth, "_post", reject)
    with pytest.raises(BrokerError):
        oauth.refresh_if_needed(account)
    account.refresh_from_db()
    assert account.connection_status == BrokerAccount.STATUS_NEEDS_REAUTH
