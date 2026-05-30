"""P4 WS-D: delete cancelled / aborted / synthetic backtests.

done and failed are protected audit history (409); active backtests must be
cancelled first (409); deletes cascade folds / days / metrics.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from apps.backtests.models import (
    Backtest,
    BacktestDay,
    BacktestFold,
    BacktestMetrics,
)

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="bd@example.com", password="supersecret")


def _client(email: str) -> APIClient:
    c = APIClient()
    token = c.post(
        reverse("login"), {"email": email, "password": "supersecret"}, format="json"
    ).data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return c


def _make_bt(user, status: str) -> Backtest:
    return Backtest.objects.create(
        user=user, name=f"bt-{status}", universe=["AAA", "BBB"],
        start_date=dt.date(2025, 1, 6), end_date=dt.date(2025, 1, 10),
        starting_cash=Decimal("100000"), status=status,
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status_val",
    [Backtest.CANCELLED, Backtest.ABORTED_BUDGET, Backtest.ABORTED_PARTIAL, Backtest.SYNTHETIC],
)
def test_delete_eligible_statuses_succeed(user, status_val: str) -> None:
    bt = _make_bt(user, status_val)
    client = _client("bd@example.com")
    resp = client.delete(reverse("backtest-detail", args=[bt.pk]))
    assert resp.status_code == 204
    assert not Backtest.objects.filter(pk=bt.pk).exists()


@pytest.mark.django_db
def test_delete_cascades_folds_days_metrics(user) -> None:
    bt = _make_bt(user, Backtest.CANCELLED)
    fold = BacktestFold.objects.create(
        backtest=bt, fold_index=0,
        is_start=dt.date(2025, 1, 6), is_end=dt.date(2025, 1, 8),
        oos_start=dt.date(2025, 1, 9), oos_end=dt.date(2025, 1, 10),
    )
    BacktestDay.objects.create(
        backtest=bt, fold=fold, date=dt.date(2025, 1, 9),
        segment="oos", cash=Decimal("100000"), portfolio_value=Decimal("100000"),
    )
    BacktestMetrics.objects.create(backtest=bt)
    client = _client("bd@example.com")
    resp = client.delete(reverse("backtest-detail", args=[bt.pk]))
    assert resp.status_code == 204
    assert not BacktestFold.objects.filter(backtest_id=bt.pk).exists()
    assert not BacktestDay.objects.filter(backtest_id=bt.pk).exists()
    assert not BacktestMetrics.objects.filter(backtest_id=bt.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("status_val", [Backtest.DONE, Backtest.FAILED])
def test_delete_protected_statuses_409(user, status_val: str) -> None:
    bt = _make_bt(user, status_val)
    client = _client("bd@example.com")
    resp = client.delete(reverse("backtest-detail", args=[bt.pk]))
    assert resp.status_code == 409
    assert Backtest.objects.filter(pk=bt.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("status_val", [Backtest.QUEUED, Backtest.RUNNING])
def test_delete_active_statuses_409(user, status_val: str) -> None:
    bt = _make_bt(user, status_val)
    client = _client("bd@example.com")
    resp = client.delete(reverse("backtest-detail", args=[bt.pk]))
    assert resp.status_code == 409
    assert Backtest.objects.filter(pk=bt.pk).exists()


@pytest.mark.django_db
def test_delete_other_users_backtest_404(user) -> None:
    bt = _make_bt(user, Backtest.CANCELLED)
    User.objects.create_user(email="intruder@example.com", password="supersecret")
    client = _client("intruder@example.com")
    resp = client.delete(reverse("backtest-detail", args=[bt.pk]))
    assert resp.status_code == 404
    assert Backtest.objects.filter(pk=bt.pk).exists()


@pytest.mark.django_db
def test_detail_get_still_works(user) -> None:
    # Promoting to RetrieveDestroyAPIView must not break the GET contract.
    bt = _make_bt(user, Backtest.DONE)
    client = _client("bd@example.com")
    resp = client.get(reverse("backtest-detail", args=[bt.pk]))
    assert resp.status_code == 200
    assert resp.data["id"] == bt.pk
