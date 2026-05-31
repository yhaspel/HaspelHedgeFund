"""BYOK for the Resend notification key.

Mirrors the data-provider BYOK suite: resolver precedence, the
`/api/me/provider-keys/` round-trip, encryption-at-rest, and the per-user
connection threading in the email channel.
"""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient

User = get_user_model()


@pytest.mark.django_db
@override_settings(RESEND_API_KEY="platform-resend-key")
def test_resolve_uses_user_byok_when_set() -> None:
    from apps.models_catalog.models import ProviderKey
    from apps.notifications.byok import resolve_resend_api_key

    u = User.objects.create_user(email="r1@x.com", password="x" * 12)
    pk = ProviderKey.objects.create(user=u)
    pk.set_key("resend", "user-resend-key")
    pk.save()

    assert resolve_resend_api_key(u) == "user-resend-key"


@pytest.mark.django_db
@override_settings(RESEND_API_KEY="platform-resend-key")
def test_resolve_falls_back_to_platform_when_unset() -> None:
    from apps.notifications.byok import resolve_resend_api_key

    u = User.objects.create_user(email="r2@x.com", password="x" * 12)
    assert resolve_resend_api_key(u) == "platform-resend-key"


@pytest.mark.django_db
def test_api_round_trip_resend_key_set_and_read() -> None:
    u = User.objects.create_user(email="r3@x.com", password="x" * 12)
    client = APIClient()
    client.force_authenticate(u)

    res = client.put("/api/me/provider-keys/", {"resend_api_key": "k-resend"}, format="json")
    assert res.status_code == 200, res.content
    assert res.json()["resend"] == "set"

    res2 = client.get("/api/me/provider-keys/")
    assert res2.status_code == 200
    assert res2.json()["resend"] == "set"


@pytest.mark.django_db
def test_resend_key_encrypted_at_rest() -> None:
    from apps.models_catalog.models import ProviderKey

    u = User.objects.create_user(email="r4@x.com", password="x" * 12)
    pk = ProviderKey.objects.create(user=u)
    pk.set_key("resend", "super-secret-resend")
    pk.save()

    row = ProviderKey.objects.get(user=u)
    assert "super-secret-resend" not in row.resend_api_key_enc
    assert row.resend_api_key_enc  # non-empty ciphertext
    assert row.has_key("resend") is True
    assert row.get_key("resend") == "super-secret-resend"


@pytest.mark.django_db
@override_settings(RESEND_API_KEY="platform-resend-key")
def test_email_connection_threads_byok_key(monkeypatch) -> None:
    """When Resend is the active backend, the channel builds a connection with
    the channel owner's resolved BYOK key."""
    from apps.models_catalog.models import ProviderKey
    from apps.notifications.channels import email as email_channel

    u = User.objects.create_user(email="r5@x.com", password="x" * 12)
    pk = ProviderKey.objects.create(user=u)
    pk.set_key("resend", "user-key-xyz")
    pk.save()

    captured: dict = {}
    monkeypatch.setattr(
        email_channel, "get_connection",
        lambda **kw: captured.update(kw) or "CONN",
    )
    with override_settings(EMAIL_BACKEND="apps.notifications.backends.ResendEmailBackend"):
        conn = email_channel._connection_for(u)
    assert conn == "CONN"
    assert captured["api_key"] == "user-key-xyz"


def test_email_connection_none_for_non_resend_backend() -> None:
    from apps.notifications.channels import email as email_channel

    with override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
        assert email_channel._connection_for(None) is None
