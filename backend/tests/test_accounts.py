import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.tasks import ping

User = get_user_model()


@pytest.fixture
def client() -> APIClient:
    return APIClient()


@pytest.mark.django_db
def test_signup_creates_user(client: APIClient) -> None:
    resp = client.post(
        reverse("signup"),
        {"email": "a@b.com", "password": "supersecret"},
        format="json",
    )
    assert resp.status_code == 201
    assert User.objects.filter(email="a@b.com").exists()


@pytest.mark.django_db
def test_login_returns_tokens(client: APIClient) -> None:
    User.objects.create_user(email="a@b.com", password="supersecret")
    resp = client.post(
        reverse("login"),
        {"email": "a@b.com", "password": "supersecret"},
        format="json",
    )
    assert resp.status_code == 200
    assert "access" in resp.data
    assert "refresh" in resp.data


@pytest.mark.django_db
def test_me_requires_auth(client: APIClient) -> None:
    assert client.get(reverse("me")).status_code == 401


@pytest.mark.django_db
def test_me_returns_user_with_token(client: APIClient) -> None:
    User.objects.create_user(email="a@b.com", password="supersecret")
    login = client.post(
        reverse("login"),
        {"email": "a@b.com", "password": "supersecret"},
        format="json",
    )
    token = login.data["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    resp = client.get(reverse("me"))
    assert resp.status_code == 200
    assert resp.data["email"] == "a@b.com"


@pytest.mark.django_db
def test_refresh_rotates_and_returns_new_tokens(client: APIClient) -> None:
    """A session stays alive while active: /auth/refresh/ must accept the
    refresh token and, with rotation enabled, hand back BOTH a new access token
    and a new (different) refresh token so the 7-day window keeps sliding."""
    User.objects.create_user(email="a@b.com", password="supersecret")
    login = client.post(
        reverse("login"),
        {"email": "a@b.com", "password": "supersecret"},
        format="json",
    )
    original_refresh = login.data["refresh"]

    resp = client.post(
        reverse("refresh"),
        {"refresh": original_refresh},
        format="json",
    )
    assert resp.status_code == 200
    assert "access" in resp.data
    # Rotation is on, so the response includes a fresh refresh token, and it is
    # not the one we sent in.
    assert "refresh" in resp.data
    assert resp.data["refresh"] != original_refresh


@pytest.mark.django_db
def test_refreshed_access_token_authorizes_me(client: APIClient) -> None:
    """The access token minted by /auth/refresh/ must actually work against a
    protected endpoint — this is the silent renewal the frontend relies on."""
    User.objects.create_user(email="a@b.com", password="supersecret")
    login = client.post(
        reverse("login"),
        {"email": "a@b.com", "password": "supersecret"},
        format="json",
    )
    refreshed = client.post(
        reverse("refresh"),
        {"refresh": login.data["refresh"]},
        format="json",
    )
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refreshed.data['access']}")
    resp = client.get(reverse("me"))
    assert resp.status_code == 200
    assert resp.data["email"] == "a@b.com"


def test_ping_task_runs_eagerly() -> None:
    result = ping.delay().get()
    assert isinstance(result, str)
    assert "T" in result


def test_health_endpoint_is_unauthenticated(client: APIClient) -> None:
    resp = client.get(reverse("health"))
    assert resp.status_code == 200
    # P4-OFF extended the probe in place (WS-1.2): still ok + unauthenticated,
    # now also the offline discriminator fields.
    assert resp.data["status"] == "ok"
    assert resp.data["offline_mode"] is False
    assert "llm" in resp.data


def test_cors_default_includes_localhost_and_127_origins() -> None:
    """Phase 00 review fix: dev CORS must allow both hostnames so browser
    probes from 127.0.0.1:4111 don't fail their preflight."""
    from django.conf import settings

    assert "http://localhost:4111" in settings.CORS_ALLOWED_ORIGINS
    assert "http://127.0.0.1:4111" in settings.CORS_ALLOWED_ORIGINS


def test_jwt_signing_key_is_at_least_32_bytes() -> None:
    """Phase 00 review fix: the dev/test sentinel must be 32+ bytes so
    SimpleJWT does not emit length warnings on every test run."""
    from django.conf import settings

    key = settings.SIMPLE_JWT["SIGNING_KEY"]
    assert len(key.encode("utf-8")) >= 32, (
        f"JWT_SIGNING_KEY must be >= 32 bytes; got {len(key.encode('utf-8'))}"
    )
