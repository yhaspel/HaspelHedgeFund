"""P4 WS-B: rerun a terminal strategy cycle.

The cycle task itself (daily_long_short_cycle) is mocked at the endpoint
boundary — it does real screener/provider work we don't want to exercise
here. We verify (a) the endpoint's gating + dispatch wiring, (b) the
_resolve_cycle_target supersede semantics that create a fresh row and stamp
the old one, and (c) that the relaxed partial-unique constraint permits a
failed row + a fresh active row for the same (strategy, as_of_date).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework.test import APIClient

from apps.portfolios.models import (
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    Universe,
)
from apps.portfolios.tasks import _resolve_cycle_target

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="cr@example.com", password="supersecret")


def _client(email: str) -> APIClient:
    c = APIClient()
    token = c.post(
        reverse("login"), {"email": email, "password": "supersecret"}, format="json"
    ).data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return c


def _strategy(user) -> PortfolioStrategy:
    universe = Universe.objects.create(name="cr-uni", is_active=True)
    portfolio = Portfolio.objects.create(
        user=user, name="strat", kind="strategy", cash_balance=Decimal("100000"),
    )
    return PortfolioStrategy.objects.create(
        user=user, name="s", universe=universe, portfolio=portfolio,
    )


# ---- endpoint gating + dispatch ------------------------------------------

@pytest.mark.django_db
def test_rerun_failed_cycle_dispatches_with_supersede(user) -> None:
    strategy = _strategy(user)
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date(2026, 5, 29), status="failed",
    )
    client = _client("cr@example.com")
    with patch("apps.portfolios.views.daily_long_short_cycle.delay") as mock_task:
        mock_task.return_value.id = "task-xyz"
        resp = client.post(
            reverse("strategy-cycle-rerun", args=[strategy.pk, target.pk])
        )
    assert resp.status_code == 202
    assert resp.data["new_target_pending"] is True
    assert resp.data["superseded_target_id"] == target.pk
    mock_task.assert_called_once_with(
        strategy.pk, "2026-05-29", force=True, supersedes_target_id=target.pk,
    )


@pytest.mark.django_db
def test_rerun_cancelled_cycle_is_eligible(user) -> None:
    strategy = _strategy(user)
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date(2026, 5, 29), status="cancelled",
    )
    client = _client("cr@example.com")
    with patch("apps.portfolios.views.daily_long_short_cycle.delay") as mock_task:
        mock_task.return_value.id = "t"
        resp = client.post(
            reverse("strategy-cycle-rerun", args=[strategy.pk, target.pk])
        )
    assert resp.status_code == 202
    assert mock_task.called


@pytest.mark.django_db
def test_rerun_done_cycle_409s(user) -> None:
    strategy = _strategy(user)
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date(2026, 5, 29), status="done",
    )
    client = _client("cr@example.com")
    with patch("apps.portfolios.views.daily_long_short_cycle.delay") as mock_task:
        resp = client.post(
            reverse("strategy-cycle-rerun", args=[strategy.pk, target.pk])
        )
    assert resp.status_code == 409
    mock_task.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("active", ["queued", "awaiting_review", "running_council", "constructing"])
def test_rerun_active_cycle_409s(user, active: str) -> None:
    strategy = _strategy(user)
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date(2026, 5, 29), status=active,
    )
    client = _client("cr@example.com")
    with patch("apps.portfolios.views.daily_long_short_cycle.delay") as mock_task:
        resp = client.post(
            reverse("strategy-cycle-rerun", args=[strategy.pk, target.pk])
        )
    assert resp.status_code == 409
    mock_task.assert_not_called()


@pytest.mark.django_db
def test_rerun_other_users_cycle_404s(user) -> None:
    strategy = _strategy(user)
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date(2026, 5, 29), status="failed",
    )
    User.objects.create_user(email="intruder@example.com", password="supersecret")
    client = _client("intruder@example.com")
    with patch("apps.portfolios.views.daily_long_short_cycle.delay") as mock_task:
        resp = client.post(
            reverse("strategy-cycle-rerun", args=[strategy.pk, target.pk])
        )
    assert resp.status_code == 404
    mock_task.assert_not_called()


# ---- _resolve_cycle_target supersede semantics ----------------------------

@pytest.mark.django_db
def test_resolve_supersede_creates_new_and_stamps_old(user) -> None:
    strategy = _strategy(user)
    old = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date(2026, 5, 29), status="failed",
    )
    new = _resolve_cycle_target(
        strategy, date(2026, 5, 29),
        supersedes_target_id=old.pk,
        defaults={"status": "screening", "target_weights": {}},
    )
    assert new.pk != old.pk
    assert new.as_of_date == date(2026, 5, 29)
    old.refresh_from_db()
    assert old.superseded_by_id == new.pk
    assert old.status == "failed"  # the old terminal row is untouched otherwise


@pytest.mark.django_db
def test_resolve_supersede_is_idempotent_across_two_calls(user) -> None:
    # The pairs flavor resolves the target twice in one cycle; the 2nd call
    # must reuse the row the 1st created, not make a 3rd row.
    strategy = _strategy(user)
    old = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date(2026, 5, 29), status="failed",
    )
    first = _resolve_cycle_target(
        strategy, date(2026, 5, 29), supersedes_target_id=old.pk,
        defaults={"status": "running_council", "target_weights": {}},
    )
    second = _resolve_cycle_target(
        strategy, date(2026, 5, 29), supersedes_target_id=old.pk,
        defaults={"status": "running", "target_weights": {}},
    )
    assert first.pk == second.pk
    assert second.status == "running"
    assert PortfolioTarget.objects.filter(
        strategy=strategy, as_of_date=date(2026, 5, 29)
    ).count() == 2  # old + the one fresh row


@pytest.mark.django_db
def test_resolve_normal_path_reuses_non_superseded_row(user) -> None:
    strategy = _strategy(user)
    live = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date(2026, 5, 29), status="screening",
    )
    got = _resolve_cycle_target(
        strategy, date(2026, 5, 29),
        defaults={"status": "constructing", "target_weights": {}},
    )
    assert got.pk == live.pk
    assert got.status == "constructing"


@pytest.mark.django_db
def test_resolve_normal_path_ignores_superseded_row(user) -> None:
    # After a rerun, the superseded row must not be picked up by the normal
    # (no-supersede) resolution — otherwise we'd resurrect the old failed row.
    strategy = _strategy(user)
    new = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date(2026, 5, 29), status="screening",
    )
    superseded = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date(2026, 5, 29), status="failed",
        superseded_by=new,
    )
    got = _resolve_cycle_target(
        strategy, date(2026, 5, 29),
        defaults={"status": "running", "target_weights": {}},
    )
    assert got.pk == new.pk
    assert got.pk != superseded.pk


# ---- relaxed partial-unique constraint ------------------------------------

@pytest.mark.django_db
def test_failed_plus_fresh_active_row_allowed(user) -> None:
    strategy = _strategy(user)
    PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date(2026, 5, 29), status="failed",
    )
    # The widened constraint excludes failed, so a fresh active row is allowed.
    PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date(2026, 5, 29), status="screening",
    )
    assert PortfolioTarget.objects.filter(
        strategy=strategy, as_of_date=date(2026, 5, 29)
    ).count() == 2


@pytest.mark.django_db
def test_two_active_rows_still_rejected(user) -> None:
    strategy = _strategy(user)
    PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date(2026, 5, 29), status="screening",
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            PortfolioTarget.objects.create(
                strategy=strategy, as_of_date=date(2026, 5, 29), status="running",
            )
