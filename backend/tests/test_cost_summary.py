"""P5-SH WS2.4 — operator LLM cost-summary API (GET /api/costs/summary/)."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


def _call(**kw):
    from hedgefund_agents.models import LLMCall

    kw.setdefault("provider", "openrouter")
    return LLMCall.objects.create(**kw)


@pytest.fixture
def seeded():
    _call(agent_name="buffett", model="m1", cost_usd=Decimal("0.10"))
    _call(agent_name="buffett", model="m1", cost_usd=Decimal("0.20"))
    _call(agent_name="news_digest", model="m2", cost_usd=Decimal("0.05"))
    # Outside the 30-day window → excluded.
    old = _call(agent_name="buffett", model="m1", cost_usd=Decimal("9.99"))
    from hedgefund_agents.models import LLMCall

    LLMCall.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=40))
    # Unknown-price sentinel (cost_usd < 0) → excluded from spend.
    _call(agent_name="x", model="m3", cost_usd=Decimal("-1"))


def _admin_client():
    admin = get_user_model().objects.create_superuser(email="admin@example.test", password="x")
    client = APIClient()
    client.force_authenticate(user=admin)
    return client


def test_cost_summary_aggregates_within_window(seeded):
    resp = _admin_client().get("/api/costs/summary/?days=30")
    assert resp.status_code == 200
    data = resp.json()

    assert Decimal(data["total_usd"]) == Decimal("0.35")  # 0.10 + 0.20 + 0.05
    assert data["days"] == 30

    by_agent = {r["agent_name"]: r for r in data["by_agent"]}
    assert Decimal(by_agent["buffett"]["cost_usd"]) == Decimal("0.30")
    assert by_agent["buffett"]["calls"] == 2
    assert Decimal(by_agent["news_digest"]["cost_usd"]) == Decimal("0.05")
    assert "x" not in by_agent  # the negative-cost sentinel is excluded

    by_model = {r["model"]: r for r in data["by_model"]}
    assert Decimal(by_model["m1"]["cost_usd"]) == Decimal("0.30")
    assert "m3" not in by_model

    # by_day_model rolls up (date, model); today has m1 (0.30) and m2 (0.05).
    today = timezone.now().date().isoformat()
    today_rows = {r["model"]: r for r in data["by_day_model"] if r["date"] == today}
    assert Decimal(today_rows["m1"]["cost_usd"]) == Decimal("0.30")


def test_cost_summary_clamps_days(seeded):
    resp = _admin_client().get("/api/costs/summary/?days=9999")
    assert resp.status_code == 200
    assert resp.json()["days"] == 365


def test_cost_summary_requires_superuser():
    user = get_user_model().objects.create_user(email="plain@example.test", password="x")
    client = APIClient()
    client.force_authenticate(user=user)
    assert client.get("/api/costs/summary/").status_code == 403
