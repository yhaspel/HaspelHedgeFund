"""Run-history full-text search (P3b). Sqlite path uses icontains; the Postgres
tsvector path + GIN index are exercised live (see migration 0011)."""
from __future__ import annotations

import datetime as dt

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.runs.models import AgentMessage, Decision, Run
from apps.runs.search import build_search_text

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="rs@x.test", password="pw-fake-123456789")


@pytest.fixture
def auth_client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def test_build_search_text_pulls_thesis_and_rationale(user):
    run = Run.objects.create(
        user=user, tickers=["AAPL"], as_of_date=dt.date(2026, 5, 1), status=Run.DONE
    )
    AgentMessage.objects.create(
        run=run, agent_name="buffett",
        parsed_output={"signal": "bullish", "thesis": "durable competitive moat",
                       "key_risks": ["regulatory overhang"]},
    )
    Decision.objects.create(run=run, ticker="AAPL", action="buy", rationale="strong fundamentals")
    txt = build_search_text(run)
    assert "AAPL" in txt
    assert "moat" in txt
    assert "regulatory overhang" in txt
    assert "strong fundamentals" in txt


def test_search_param_filters(user, auth_client):
    base = dict(user=user, as_of_date=dt.date(2026, 5, 1), status=Run.DONE)
    hit = Run.objects.create(tickers=["AAPL"], search_text="apple semiconductor moat", **base)
    miss = Run.objects.create(tickers=["XOM"], search_text="oil energy dividend", **base)

    resp = auth_client.get("/api/runs/?search=semiconductor")
    assert resp.status_code == 200
    ids = [row["id"] for row in resp.json()]
    assert hit.id in ids
    assert miss.id not in ids


def test_no_search_returns_all(user, auth_client):
    Run.objects.create(user=user, tickers=["AAPL"], as_of_date=dt.date(2026, 5, 1), status=Run.DONE)
    resp = auth_client.get("/api/runs/")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
