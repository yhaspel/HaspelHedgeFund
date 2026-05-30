"""P4 WS-A: rerun a terminal Analysis run.

The Celery task is mocked — we verify the new Run row copies the original's
payload, links back via rerun_of, and that gating (409 on active, 404 on
cross-user) and serializer provenance (rerun_of / reruns) are correct.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from apps.portfolios.models import (
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    Universe,
)
from apps.runs.models import Run

User = get_user_model()


def _client(email: str) -> APIClient:
    User.objects.create_user(email=email, password="supersecret")
    c = APIClient()
    token = c.post(
        reverse("login"), {"email": email, "password": "supersecret"}, format="json"
    ).data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return c


@pytest.fixture
def auth_client() -> APIClient:
    return _client("rerun@example.com")


def _make_run(user, **kwargs) -> Run:
    defaults = dict(
        tickers=["AAPL"],
        model_overrides={"persona": "openrouter:foo"},
        as_of_date=date(2024, 12, 31),
        personas=["buffett"],
        status=Run.FAILED,
    )
    defaults.update(kwargs)
    return Run.objects.create(user=user, **defaults)


@pytest.mark.django_db
def test_rerun_failed_adhoc_preserves_payload(auth_client: APIClient) -> None:
    user = User.objects.get(email="rerun@example.com")
    original = _make_run(user)
    with patch("apps.runs.views.execute_run.delay") as mock_task:
        mock_task.return_value.id = "task-123"
        resp = auth_client.post(reverse("run-rerun", args=[original.pk]))
    assert resp.status_code == 201
    assert resp.data["rerun_of"] == original.pk
    assert resp.data["status"] == "queued"
    new_run = Run.objects.get(pk=resp.data["id"])
    assert new_run.pk != original.pk
    assert new_run.tickers == ["AAPL"]
    assert new_run.model_overrides == {"persona": "openrouter:foo"}
    assert new_run.as_of_date == date(2024, 12, 31)
    assert new_run.personas == ["buffett"]
    assert new_run.source == Run.ADHOC
    assert new_run.rerun_of_id == original.pk
    assert new_run.celery_task_id == "task-123"
    mock_task.assert_called_once_with(new_run.pk)
    # The original is untouched (still failed evidence).
    original.refresh_from_db()
    assert original.status == Run.FAILED


@pytest.mark.django_db
def test_rerun_empty_personas_preserved_as_all(auth_client: APIClient) -> None:
    user = User.objects.get(email="rerun@example.com")
    original = _make_run(user, personas=[])
    with patch("apps.runs.views.execute_run.delay") as mock_task:
        mock_task.return_value.id = "t"
        resp = auth_client.post(reverse("run-rerun", args=[original.pk]))
    new_run = Run.objects.get(pk=resp.data["id"])
    # Empty list means "all" to execute_run — must not be normalized away.
    assert new_run.personas == []


@pytest.mark.django_db
def test_rerun_strategy_sourced_keeps_source_and_target(auth_client: APIClient) -> None:
    user = User.objects.get(email="rerun@example.com")
    universe = Universe.objects.create(name="rerun-uni", is_active=True)
    portfolio = Portfolio.objects.create(
        user=user, name="strat", kind="strategy", cash_balance=Decimal("100000"),
    )
    strategy = PortfolioStrategy.objects.create(
        user=user, name="s", universe=universe, portfolio=portfolio,
    )
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date(2024, 12, 31), status="failed",
    )
    original = _make_run(
        user, source=Run.STRATEGY, portfolio_target=target, tickers=["NVDA"],
    )
    with patch("apps.runs.views.execute_run.delay") as mock_task:
        mock_task.return_value.id = "t"
        resp = auth_client.post(reverse("run-rerun", args=[original.pk]))
    assert resp.status_code == 201
    new_run = Run.objects.get(pk=resp.data["id"])
    assert new_run.source == Run.STRATEGY
    assert new_run.portfolio_target_id == target.pk
    assert new_run.rerun_of_id == original.pk


@pytest.mark.django_db
@pytest.mark.parametrize("active_status", [Run.QUEUED, Run.RUNNING])
def test_rerun_active_run_409s(auth_client: APIClient, active_status: str) -> None:
    user = User.objects.get(email="rerun@example.com")
    original = _make_run(user, status=active_status)
    with patch("apps.runs.views.execute_run.delay") as mock_task:
        resp = auth_client.post(reverse("run-rerun", args=[original.pk]))
    assert resp.status_code == 409
    mock_task.assert_not_called()


@pytest.mark.django_db
def test_rerun_other_users_run_404s(auth_client: APIClient) -> None:
    other = User.objects.create_user(email="other@example.com", password="x")
    original = _make_run(other)
    with patch("apps.runs.views.execute_run.delay") as mock_task:
        resp = auth_client.post(reverse("run-rerun", args=[original.pk]))
    assert resp.status_code == 404
    mock_task.assert_not_called()


@pytest.mark.django_db
def test_rerun_chain_renders_on_summary_and_detail(auth_client: APIClient) -> None:
    user = User.objects.get(email="rerun@example.com")
    original = _make_run(user)
    with patch("apps.runs.views.execute_run.delay") as mock_task:
        mock_task.return_value.id = "t"
        rerun_resp = auth_client.post(reverse("run-rerun", args=[original.pk]))
    new_id = rerun_resp.data["id"]

    # Summary (list) carries rerun_of on the child row.
    listing = auth_client.get(reverse("run-list-create"))
    rows = {r["id"]: r for r in listing.data}
    assert rows[new_id]["rerun_of"] == original.pk
    assert rows[original.pk]["rerun_of"] is None

    # Detail of the original lists its reruns; detail of the child shows its parent.
    parent_detail = auth_client.get(reverse("run-detail", args=[original.pk]))
    assert parent_detail.data["reruns"] == [new_id]
    assert parent_detail.data["rerun_of"] is None
    child_detail = auth_client.get(reverse("run-detail", args=[new_id]))
    assert child_detail.data["rerun_of"] == original.pk
    assert child_detail.data["reruns"] == []
