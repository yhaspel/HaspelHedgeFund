"""WP-B2 regression tests for fixes that had no proof test of their own."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.runs.models import Run

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="b2@example.test", password="supersecret")


def _run(user, **kw):
    defaults = dict(tickers=["AAPL"], as_of_date=dt.date(2026, 6, 1), status=Run.QUEUED)
    defaults.update(kw)
    return Run.objects.create(user=user, **defaults)


# ---------------------------------------------------------------------------
# execute_run seeds state["news"] so the sentiment node has something to score
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_execute_run_seeds_a_non_empty_news_batch(user):
    from apps.data.models import NewsItem
    from apps.runs.tasks import execute_run
    from hedgefund_agents.analytical.sentiment import NewsBatch

    item = NewsItem(
        ticker="AAPL", published_at=timezone.make_aware(dt.datetime(2026, 5, 30, 9, 0)),
        headline="AAPL raises guidance", source="reuters", provider="tiingo",
        url="https://example.test/seed", summary="Raised FY outlook.",
    )

    class _Svc:
        def fetch_and_persist(self, ticker, *, as_of, lookback_days=30):
            return [item]

    run = _run(user)
    graph = MagicMock()
    graph.invoke.return_value = {}
    with patch("apps.runs.tasks.resolve_graph", return_value=graph), \
         patch("apps.runs.tasks.get_fmp_provider", return_value=MagicMock()), \
         patch("apps.runs.tasks.get_edgar_provider", return_value=MagicMock()), \
         patch("apps.runs.tasks.get_ownership_provider", return_value=MagicMock()), \
         patch("apps.data.providers.factory.get_news_service", return_value=_Svc()):
        execute_run(run.id)

    state = graph.invoke.call_args[0][0]
    news = state["news"]
    assert isinstance(news, NewsBatch)
    assert news.headlines == ["AAPL raises guidance"]
    assert not news.is_empty()


# ---------------------------------------------------------------------------
# An unexpected node exception is NAMED on the transcript, not passed silently
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_unexpected_node_failure_marks_the_agent_message_error(user):
    from apps.runs.models import AgentMessage
    from apps.runs.tasks import _persist_outputs
    from hedgefund_agents.graphs._node_fallback import wrap_backtest_tolerant

    def boom(state):
        raise ZeroDivisionError("bad math")

    out = wrap_backtest_tolerant(boom, "technicals")({"ticker": "AAPL"})
    assert out["technicals"]["_error"] == "ZeroDivisionError"

    run = _run(user, status=Run.RUNNING)
    _persist_outputs(run, out, [])
    msg = AgentMessage.objects.get(run=run, agent_name="technicals")
    assert msg.status == "error"
    assert msg.raw_response == "ZeroDivisionError"
    assert "_error" not in msg.parsed_output and "_degraded" not in msg.parsed_output


@pytest.mark.django_db
def test_news_node_names_an_unexpected_exception_but_not_a_parse_one(user):
    """The narrowed `except` in run_news: a parse failure is an expected LLM
    outcome (skeletal digest, status stays clean); anything else is named."""
    from hedgefund_agents.llm.client import LLMResponse
    from hedgefund_agents.llm.structured import StructuredOutputError
    from hedgefund_agents.news import news_agent

    class _Svc:
        def fetch_and_persist(self, *a, **k):
            return []

    def _run_with(exc):
        class _C:
            provider = "fake"

            def complete(self, **kw):
                raise exc

        state = {"ticker": "AAPL", "as_of_date": dt.date(2026, 6, 1), "run_id": None}
        with patch.object(news_agent, "get_news_service", return_value=_Svc()), \
             patch.object(news_agent, "_risk_factors_excerpt", return_value=""), \
             patch.object(news_agent, "get_llm", return_value=_C()):
            return news_agent.run_news(state)["news_digest"]

    parse = _run_with(StructuredOutputError(
        "bad json", LLMResponse(text="x", model="m", provider="fake")))
    assert "_error" not in parse
    assert parse["_freshness"]["fallback"] is True

    weird = _run_with(ZeroDivisionError("bad math"))
    assert weird["_error"] == "ZeroDivisionError"
    assert weird["_freshness"]["fallback"] is True


# ---------------------------------------------------------------------------
# Settings › Models per-run cost ceiling, wired for AD-HOC runs (owner decision)
# ---------------------------------------------------------------------------

def _prefs(user, ceiling):
    from apps.models_catalog.models import UserModelPreferences

    obj, _ = UserModelPreferences.objects.get_or_create(user=user)
    obj.cost_ceiling_per_run_usd = ceiling
    obj.save(update_fields=["cost_ceiling_per_run_usd"])
    return obj


def _post_run(user, **extra):
    from django.urls import reverse
    from rest_framework.test import APIClient

    c = APIClient()
    c.force_authenticate(user)
    payload = {"tickers": ["AAPL"], "as_of_date": "2026-06-01"}
    payload.update(extra)
    with patch("apps.runs.views.execute_run.delay") as d:
        d.return_value = MagicMock(id="t1")
        return c.post(reverse("run-list-create"), payload, format="json")


@pytest.mark.django_db
def test_run_create_rejects_a_projection_over_the_per_run_ceiling(user):
    _prefs(user, Decimal("0.0100"))
    with patch("apps.runs.views.estimate_run_cost_usd", return_value=1.2345):
        resp = _post_run(user)
    assert resp.status_code == 400
    assert resp.data["detail"] == (
        "Projected cost $1.23 exceeds your per-run ceiling $0.01 (Settings › Models)."
    )
    assert not Run.objects.exists()


@pytest.mark.django_db
def test_run_create_seeds_max_budget_from_the_ceiling(user):
    _prefs(user, Decimal("2.5000"))
    with patch("apps.runs.views.estimate_run_cost_usd", return_value=0.10):
        resp = _post_run(user)
    assert resp.status_code == 201
    assert Run.objects.get(pk=resp.data["id"]).max_budget_usd == Decimal("2.50")


@pytest.mark.django_db
def test_an_explicit_budget_wins_over_the_ceiling_seed(user):
    _prefs(user, Decimal("2.5000"))
    with patch("apps.runs.views.estimate_run_cost_usd", return_value=0.10):
        resp = _post_run(user, max_budget_usd="0.75")
    assert resp.status_code == 201
    assert Run.objects.get(pk=resp.data["id"]).max_budget_usd == Decimal("0.75")


@pytest.mark.django_db
def test_no_ceiling_means_no_gate_and_no_seed(user):
    _prefs(user, None)
    with patch("apps.runs.views.estimate_run_cost_usd", return_value=999.0) as est:
        resp = _post_run(user)
    assert resp.status_code == 201
    assert not est.called
    assert Run.objects.get(pk=resp.data["id"]).max_budget_usd is None


@pytest.mark.django_db
def test_estimate_run_cost_is_positive_and_scales_with_personas(user):
    from apps.runs.views import estimate_run_cost_usd

    one = estimate_run_cost_usd(personas=["buffett"], model_overrides={})
    three = estimate_run_cost_usd(personas=["buffett", "munger", "graham"],
                                  model_overrides={})
    assert one > 0
    assert three > one


@pytest.mark.django_db
def test_execute_run_news_seed_is_best_effort(user):
    """A provider outage must not fail the run — sentiment just stays neutral."""
    from apps.runs.tasks import execute_run
    from hedgefund_agents.analytical.sentiment import NewsBatch

    class _Boom:
        def fetch_and_persist(self, *a, **k):
            raise RuntimeError("tiingo down")

    run = _run(user)
    graph = MagicMock()
    graph.invoke.return_value = {}
    with patch("apps.runs.tasks.resolve_graph", return_value=graph), \
         patch("apps.runs.tasks.get_fmp_provider", return_value=MagicMock()), \
         patch("apps.runs.tasks.get_edgar_provider", return_value=MagicMock()), \
         patch("apps.runs.tasks.get_ownership_provider", return_value=MagicMock()), \
         patch("apps.data.providers.factory.get_news_service", return_value=_Boom()):
        execute_run(run.id)

    run.refresh_from_db()
    assert run.status == Run.DONE
    assert graph.invoke.call_args[0][0]["news"] == NewsBatch.empty()
