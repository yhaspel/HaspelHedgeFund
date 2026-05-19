"""API contract tests for the runs endpoints. The Celery task is mocked
so we don't actually call providers — we're verifying the lifecycle wiring.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

User = get_user_model()


@pytest.fixture
def auth_client() -> APIClient:
    User.objects.create_user(email="a@b.com", password="supersecret")
    c = APIClient()
    token = c.post(reverse("login"), {"email": "a@b.com", "password": "supersecret"},
                   format="json").data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return c


@pytest.mark.django_db
def test_create_run_enqueues_celery_task(auth_client: APIClient) -> None:
    with patch("apps.runs.views.execute_run.delay") as mock_task:
        resp = auth_client.post(
            reverse("run-list-create"),
            {"tickers": ["aapl"], "as_of_date": "2024-12-31"},
            format="json",
        )
    assert resp.status_code == 201
    assert mock_task.called
    assert resp.data["tickers"] == ["AAPL"]


@pytest.mark.django_db
def test_run_detail_requires_owner(auth_client: APIClient) -> None:
    with patch("apps.runs.views.execute_run.delay"):
        created = auth_client.post(
            reverse("run-list-create"),
            {"tickers": ["AAPL"], "as_of_date": "2024-12-31"},
            format="json",
        )
    rid = created.data["id"]
    detail = auth_client.get(reverse("run-detail", args=[rid]))
    assert detail.status_code == 200
    assert detail.data["status"] == "queued"


@pytest.mark.django_db
def test_cancel_active_run_revokes_and_marks_cancelled(auth_client: APIClient) -> None:
    with patch("apps.runs.views.execute_run.delay") as mock_task:
        mock_task.return_value.id = "fake-task-id"
        created = auth_client.post(
            reverse("run-list-create"),
            {"tickers": ["AAPL"], "as_of_date": "2024-12-31"},
            format="json",
        )
    rid = created.data["id"]
    with patch("apps.runs.views.celery_app.control.revoke") as mock_revoke:
        resp = auth_client.post(reverse("run-cancel", args=[rid]))
    assert resp.status_code == 200
    assert resp.data["status"] == "cancelled"
    mock_revoke.assert_called_once_with("fake-task-id", terminate=True, signal="SIGTERM")


@pytest.mark.django_db
def test_cancel_terminal_run_409s(auth_client: APIClient) -> None:
    from apps.runs.models import Run
    with patch("apps.runs.views.execute_run.delay"):
        created = auth_client.post(
            reverse("run-list-create"),
            {"tickers": ["AAPL"], "as_of_date": "2024-12-31"},
            format="json",
        )
    rid = created.data["id"]
    Run.objects.filter(pk=rid).update(status=Run.DONE)
    with patch("apps.runs.views.celery_app.control.revoke") as mock_revoke:
        resp = auth_client.post(reverse("run-cancel", args=[rid]))
    assert resp.status_code == 409
    mock_revoke.assert_not_called()


@pytest.mark.django_db
def test_models_catalog_lists_at_least_two_providers(auth_client: APIClient) -> None:
    # GET /api/models/ moved to the models_catalog app in P2d.
    resp = auth_client.get(reverse("models-catalog"))
    assert resp.status_code == 200
    providers = {m["provider"] for m in resp.data["models"]}
    assert {"anthropic", "openrouter"}.issubset(providers)


@pytest.mark.django_db
def test_create_run_rejects_unknown_persona(auth_client: APIClient) -> None:
    with patch("apps.runs.views.execute_run.delay") as mock_task:
        resp = auth_client.post(
            reverse("run-list-create"),
            {
                "tickers": ["AAPL"],
                "as_of_date": "2024-12-31",
                "personas": ["buffett", "not_a_real_persona"],
            },
            format="json",
        )
    assert resp.status_code == 400
    assert "personas" in resp.data
    mock_task.assert_not_called()


@pytest.mark.django_db
def test_create_run_rejects_duplicate_persona(auth_client: APIClient) -> None:
    with patch("apps.runs.views.execute_run.delay"):
        resp = auth_client.post(
            reverse("run-list-create"),
            {
                "tickers": ["AAPL"],
                "as_of_date": "2024-12-31",
                "personas": ["buffett", "buffett"],
            },
            format="json",
        )
    assert resp.status_code == 400
    assert "personas" in resp.data
