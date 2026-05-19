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


def test_ping_task_runs_eagerly() -> None:
    result = ping.delay().get()
    assert isinstance(result, str)
    assert "T" in result


def test_health_endpoint_is_unauthenticated(client: APIClient) -> None:
    resp = client.get(reverse("health"))
    assert resp.status_code == 200
    assert resp.data == {"status": "ok"}
