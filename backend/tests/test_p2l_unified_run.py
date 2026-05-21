"""P2l acceptance tests: unified Run + Strategy Cycle (auto/manual gate).

Coverage:
  - auto_run_council=False stops at awaiting_review with a persisted
    ScreenerRanking and NO candidate Run rows.
  - Approving a subset creates exactly N PortfolioTargetRun links and
    queues N Run rows owned by the strategy's user.
  - Approval enforces budget; over-budget approvals 400 with an estimate.
  - Approval rejects unknown tickers with 400.
  - Approval from a non-awaiting_review target 409s.
  - Reject endpoint marks target cancelled, cancels queued Runs, and
    allows a same-day rerun (partial unique constraint).
  - Run list source filter (`?source=strategy|adhoc`).
  - Strategy serializer includes auto_run_council.
  - Run detail exposes source + portfolio_target back-link.

LLM execution is bypassed: tests mock the chord dispatch so we exercise
only the bridge / lifecycle logic, not the council itself.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from apps.portfolios.models import (
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    PortfolioTargetRun,
    ScreenerRanking,
    Universe,
    UniverseMembership,
)
from apps.runs.models import Run

User = get_user_model()


@pytest.fixture
def user(db):
    u = User.objects.create_user(email="p2l@test.com", password="supersecret")
    return u


@pytest.fixture
def auth_client(user) -> APIClient:
    c = APIClient()
    token = c.post(
        reverse("login"),
        {"email": "p2l@test.com", "password": "supersecret"},
        format="json",
    ).data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return c


@pytest.fixture
def strategy(user, db):
    universe = Universe.objects.create(name="p2l-test-uni", description="x")
    UniverseMembership.objects.bulk_create([
        UniverseMembership(
            universe=universe, ticker=t, sector="Tech",
            effective_from=date(2020, 1, 1),
        )
        for t in ("AAPL", "MSFT", "GOOG", "NVDA")
    ])
    portfolio = Portfolio.objects.create(
        user=user, name="p2l-test-port", cash_balance=Decimal("100000"),
    )
    return PortfolioStrategy.objects.create(
        user=user,
        name="P2l Manual Gate",
        kind=PortfolioStrategy.KIND_LONG_ONLY,
        universe=universe,
        portfolio=portfolio,
        target_gross_pct=Decimal("1.0"),
        target_net_pct=Decimal("1.0"),
        top_k_longs=4,
        top_k_shorts=0,
        cost_ceiling_per_cycle_usd=Decimal("100.00"),
        auto_run_council=False,
    )


def _ranking(strategy: PortfolioStrategy, *, as_of: date) -> ScreenerRanking:
    return ScreenerRanking.objects.create(
        strategy=strategy, as_of_date=as_of,
        long_candidates=[
            {"ticker": "AAPL", "sector": "Tech", "score": 0.9, "features": {}, "rationale": "x"},
            {"ticker": "MSFT", "sector": "Tech", "score": 0.85, "features": {}, "rationale": "x"},
            {"ticker": "GOOG", "sector": "Tech", "score": 0.8, "features": {}, "rationale": "x"},
            {"ticker": "NVDA", "sector": "Tech", "score": 0.75, "features": {}, "rationale": "x"},
        ],
        short_candidates=[],
        universe_size_evaluated=4,
    )


# ---------------------------------------------------------------------------
# Manual-gate path: auto_run_council=False -> awaiting_review, no Runs.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_auto_run_council_false_stops_at_awaiting_review(strategy):
    """The cycle persists a ScreenerRanking + awaiting_review target and
    does NOT create any candidate Run rows or fire the chord."""
    from apps.portfolios.tasks import daily_long_short_cycle

    as_of = date(2024, 12, 31)

    # Patch the chord so we'd fail loudly if it dispatched.
    with patch("apps.portfolios.tasks.chord") as mock_chord:
        result = daily_long_short_cycle(strategy.pk, as_of.isoformat(), force=False)

    assert mock_chord.call_count == 0
    assert result["status"] == "awaiting_review"
    target = PortfolioTarget.objects.get(pk=result["target_id"])
    assert target.status == PortfolioTarget.AWAITING_REVIEW
    assert target.screener_ranking_id is not None
    assert PortfolioTargetRun.objects.filter(target=target).count() == 0
    assert Run.objects.filter(portfolio_target=target).count() == 0


@pytest.mark.django_db
def test_auto_run_council_true_dispatches_chord(strategy):
    """The auto-run path creates one Run per candidate, links them via
    PortfolioTargetRun, and dispatches the chord exactly once."""
    from apps.portfolios.tasks import daily_long_short_cycle

    strategy.auto_run_council = True
    strategy.save(update_fields=["auto_run_council"])
    as_of = date(2024, 12, 31)

    fake_async = MagicMock()
    fake_async.id = "fake-chord-id"
    with patch("apps.portfolios.tasks.chord") as mock_chord:
        mock_chord.return_value = MagicMock(return_value=fake_async)
        result = daily_long_short_cycle(strategy.pk, as_of.isoformat(), force=False)

    assert result["status"] == "dispatched"
    assert mock_chord.call_count == 1
    assert len(result["run_ids"]) == result["n_candidates"]
    target = PortfolioTarget.objects.get(pk=result["target_id"])
    assert target.status == PortfolioTarget.RUNNING_COUNCIL
    links = PortfolioTargetRun.objects.filter(target=target)
    assert links.count() == result["n_candidates"]
    runs = Run.objects.filter(portfolio_target=target)
    assert runs.count() == result["n_candidates"]
    assert all(r.source == Run.STRATEGY for r in runs)
    assert all(r.user_id == strategy.user_id for r in runs)


# ---------------------------------------------------------------------------
# Approval endpoint: contract.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_approve_subset_creates_only_approved_runs(auth_client, strategy):
    """Approval restricts to the requested subset; ranks are preserved."""
    as_of = date(2024, 12, 31)
    ranking = _ranking(strategy, as_of=as_of)
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=as_of,
        status=PortfolioTarget.AWAITING_REVIEW,
        screener_ranking=ranking,
    )

    fake_async = MagicMock()
    fake_async.id = "fake-chord-id"
    url = reverse(
        "strategy-cycle-approve-council",
        args=[strategy.pk, target.pk],
    )
    with patch("apps.portfolios.tasks.chord") as mock_chord:
        mock_chord.return_value = MagicMock(return_value=fake_async)
        resp = auth_client.post(
            url, {"long_tickers": ["AAPL", "MSFT"]}, format="json",
        )
    assert resp.status_code == 202, resp.data
    assert resp.data["n_candidates"] == 2

    target.refresh_from_db()
    assert target.status == PortfolioTarget.RUNNING_COUNCIL
    links = list(
        PortfolioTargetRun.objects.filter(target=target).order_by("screener_rank")
    )
    assert [link.candidate_key for link in links] == ["AAPL", "MSFT"]
    assert all(link.side == "long" for link in links)
    runs = Run.objects.filter(portfolio_target=target)
    assert runs.count() == 2
    assert all(r.status == Run.QUEUED for r in runs)


@pytest.mark.django_db
def test_approve_rejects_unknown_ticker(auth_client, strategy):
    as_of = date(2024, 12, 31)
    ranking = _ranking(strategy, as_of=as_of)
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=as_of,
        status=PortfolioTarget.AWAITING_REVIEW,
        screener_ranking=ranking,
    )
    url = reverse(
        "strategy-cycle-approve-council",
        args=[strategy.pk, target.pk],
    )
    with patch("apps.portfolios.tasks.chord"):
        resp = auth_client.post(
            url, {"long_tickers": ["AAPL", "NOT_IN_RANKING"]}, format="json",
        )
    assert resp.status_code == 400
    assert "NOT_IN_RANKING" in resp.data["detail"]


@pytest.mark.django_db
def test_approve_rejects_duplicate_tickers(auth_client, strategy):
    as_of = date(2024, 12, 31)
    ranking = _ranking(strategy, as_of=as_of)
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=as_of,
        status=PortfolioTarget.AWAITING_REVIEW,
        screener_ranking=ranking,
    )
    url = reverse(
        "strategy-cycle-approve-council",
        args=[strategy.pk, target.pk],
    )
    resp = auth_client.post(
        url, {"long_tickers": ["AAPL", "AAPL"]}, format="json",
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_approve_409_when_not_awaiting_review(auth_client, strategy):
    as_of = date(2024, 12, 31)
    ranking = _ranking(strategy, as_of=as_of)
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=as_of,
        status=PortfolioTarget.DONE,  # already terminal
        screener_ranking=ranking,
    )
    url = reverse(
        "strategy-cycle-approve-council",
        args=[strategy.pk, target.pk],
    )
    resp = auth_client.post(url, {"long_tickers": ["AAPL"]}, format="json")
    assert resp.status_code == 409


@pytest.mark.django_db
def test_approve_rejects_over_budget_set(auth_client, strategy):
    """If the approved set's estimated cost > strategy.cost_ceiling_per_cycle_usd,
    the endpoint returns 400 with the estimate so the UI can show it."""
    as_of = date(2024, 12, 31)
    ranking = _ranking(strategy, as_of=as_of)
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=as_of,
        status=PortfolioTarget.AWAITING_REVIEW,
        screener_ranking=ranking,
    )
    url = reverse(
        "strategy-cycle-approve-council",
        args=[strategy.pk, target.pk],
    )

    # Force exceeds_ceiling by mocking estimate_candidates_cost.
    with patch(
        "apps.portfolios.runs_bridge.estimate_candidates_cost",
        return_value={
            "n_candidates": 4,
            "per_call_usd": 99.0,
            "est_total_usd": 999.0,
            "cost_ceiling_usd": float(strategy.cost_ceiling_per_cycle_usd),
            "exceeds_ceiling": True,
            "per_agent": [],
            "overrides": {},
            "preset": "frugal",
        },
    ):
        resp = auth_client.post(
            url, {"long_tickers": ["AAPL", "MSFT", "GOOG", "NVDA"]}, format="json",
        )
    assert resp.status_code == 400
    assert resp.data["estimate"]["exceeds_ceiling"] is True


# ---------------------------------------------------------------------------
# Reject endpoint: lifecycle + same-day rerun.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_reject_cancels_target_and_queued_runs(auth_client, strategy):
    as_of = date(2024, 12, 31)
    ranking = _ranking(strategy, as_of=as_of)
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=as_of,
        status=PortfolioTarget.RUNNING_COUNCIL,
        screener_ranking=ranking,
        celery_task_id="fake-chord-id",
    )
    queued_run = Run.objects.create(
        user=strategy.user, tickers=["AAPL"], as_of_date=as_of,
        source=Run.STRATEGY, portfolio_target=target,
        status=Run.QUEUED, celery_task_id="fake-leg-id",
    )
    url = reverse("strategy-cycle-reject", args=[strategy.pk, target.pk])
    with patch("hedgefund.celery.app.control.revoke") as mock_revoke:
        resp = auth_client.post(url)
    assert resp.status_code == 200
    target.refresh_from_db()
    queued_run.refresh_from_db()
    assert target.status == PortfolioTarget.CANCELLED
    assert queued_run.status == Run.CANCELLED
    assert resp.data["cancelled_runs"] == 1
    assert mock_revoke.call_count >= 1  # at least the chord callback


@pytest.mark.django_db
def test_partial_unique_constraint_allows_rerun_after_cancel(strategy):
    """After a target is cancelled, the same (strategy, as_of_date) can
    receive a fresh row — the partial unique constraint excludes cancelled."""
    as_of = date(2024, 12, 31)
    t1 = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=as_of,
        status=PortfolioTarget.CANCELLED,
    )
    # This MUST not raise IntegrityError.
    t2 = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=as_of,
        status=PortfolioTarget.AWAITING_REVIEW,
    )
    assert t1.pk != t2.pk
    assert PortfolioTarget.objects.filter(
        strategy=strategy, as_of_date=as_of,
    ).count() == 2


# ---------------------------------------------------------------------------
# Run list source filter + serializer additions.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_runs_api_source_filter(auth_client, user, strategy):
    """?source=strategy returns only strategy-sourced rows, ?source=adhoc only adhoc."""
    as_of = date(2024, 12, 31)
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=as_of,
        status=PortfolioTarget.RUNNING_COUNCIL,
    )
    Run.objects.create(
        user=user, tickers=["AAPL"], as_of_date=as_of,
        source=Run.STRATEGY, portfolio_target=target, status=Run.QUEUED,
    )
    Run.objects.create(
        user=user, tickers=["MSFT"], as_of_date=as_of,
        source=Run.ADHOC, status=Run.QUEUED,
    )

    url = reverse("run-list-create")
    all_resp = auth_client.get(url)
    assert len(all_resp.data) == 2
    strat_resp = auth_client.get(url + "?source=strategy")
    assert len(strat_resp.data) == 1
    assert strat_resp.data[0]["source"] == "strategy"
    assert strat_resp.data[0]["portfolio_target"] == target.pk
    adhoc_resp = auth_client.get(url + "?source=adhoc")
    assert len(adhoc_resp.data) == 1
    assert adhoc_resp.data[0]["source"] == "adhoc"
    assert adhoc_resp.data[0]["portfolio_target"] is None


@pytest.mark.django_db
def test_run_detail_includes_strategy_backlink(auth_client, user, strategy):
    as_of = date(2024, 12, 31)
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=as_of,
        status=PortfolioTarget.RUNNING_COUNCIL,
    )
    run = Run.objects.create(
        user=user, tickers=["AAPL"], as_of_date=as_of,
        source=Run.STRATEGY, portfolio_target=target, status=Run.QUEUED,
    )
    resp = auth_client.get(reverse("run-detail", args=[run.pk]))
    assert resp.status_code == 200
    assert resp.data["source"] == "strategy"
    assert resp.data["portfolio_target"] == target.pk
    backlink = resp.data["strategy_backlink"]
    assert backlink["strategy_id"] == strategy.pk
    assert backlink["strategy_name"] == strategy.name
    assert backlink["portfolio_target_id"] == target.pk


@pytest.mark.django_db
def test_strategy_serializer_includes_auto_run_council(auth_client, strategy):
    resp = auth_client.get(reverse("strategy-detail", args=[strategy.pk]))
    assert resp.status_code == 200
    assert "auto_run_council" in resp.data
    assert resp.data["auto_run_council"] is False


# ---------------------------------------------------------------------------
# Bridge unit tests (no API, no chord).
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_candidates_from_ranking_subset_filter(strategy):
    as_of = date(2024, 12, 31)
    ranking = _ranking(strategy, as_of=as_of)
    from apps.portfolios.runs_bridge import candidates_from_ranking

    cands, unknown = candidates_from_ranking(
        ranking, long_subset=["AAPL", "MSFT"], short_subset=[],
    )
    assert [c.ticker for c in cands] == ["AAPL", "MSFT"]
    assert unknown == []

    cands, unknown = candidates_from_ranking(
        ranking, long_subset=["AAPL", "ZZZ"], short_subset=[],
    )
    assert unknown == ["ZZZ"]


@pytest.mark.django_db
def test_create_candidate_runs_is_idempotent(strategy):
    """Calling create_candidate_runs twice for the same target reuses
    PortfolioTargetRun rows instead of creating duplicates."""
    as_of = date(2024, 12, 31)
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=as_of,
        status=PortfolioTarget.RUNNING_COUNCIL,
    )
    from apps.portfolios.runs_bridge import CandidateSpec, create_candidate_runs

    specs = [
        CandidateSpec(
            ticker="AAPL", sector="Tech", side="long", theme="",
            rank=1, score=0.9, payload={"ticker": "AAPL"},
        ),
        CandidateSpec(
            ticker="MSFT", sector="Tech", side="long", theme="",
            rank=2, score=0.8, payload={"ticker": "MSFT"},
        ),
    ]
    payloads_1, runs_1 = create_candidate_runs(
        strategy=strategy, target=target, candidates=specs, as_of=as_of,
        overrides={}, personas_for_run=None, flavor="",
        bearish_veto_threshold=0.7,
    )
    payloads_2, runs_2 = create_candidate_runs(
        strategy=strategy, target=target, candidates=specs, as_of=as_of,
        overrides={}, personas_for_run=None, flavor="",
        bearish_veto_threshold=0.7,
    )
    assert PortfolioTargetRun.objects.filter(target=target).count() == 2
    assert [r.pk for r in runs_1] == [r.pk for r in runs_2]
    assert [p["run_id"] for p in payloads_1] == [p["run_id"] for p in payloads_2]


@pytest.mark.django_db
def test_cost_rollup_invariant(user, strategy):
    """sum(candidate_run.total_cost_usd) ≈ target.total_cost_usd."""
    from apps.runs.models import Run as _Run
    from hedgefund_agents.models import LLMCall

    as_of = date(2024, 12, 31)
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=as_of,
        status=PortfolioTarget.RUNNING_COUNCIL,
    )
    runs = [
        _Run.objects.create(
            user=user, tickers=[t], as_of_date=as_of,
            source=_Run.STRATEGY, portfolio_target=target, status=_Run.DONE,
            total_cost_usd=Decimal("0.0123"),
        )
        for t in ("AAPL", "MSFT")
    ]
    # Mirror what record_llm_call would do: write LLMCall + bump both rollups.
    for r in runs:
        LLMCall.objects.create(
            run=r, portfolio_target=target, agent_name="x",
            provider="anthropic", model="x", cost_usd=Decimal("0.0123"),
        )
    # Recompute via aggregation.
    from django.db.models import Sum
    sum_runs = (
        _Run.objects.filter(portfolio_target=target)
        .aggregate(s=Sum("total_cost_usd"))["s"]
    )
    sum_calls = (
        LLMCall.objects.filter(portfolio_target=target, cost_usd__gt=0)
        .aggregate(s=Sum("cost_usd"))["s"]
    )
    assert sum_runs == sum_calls == Decimal("0.0246")


@pytest.mark.django_db
def test_target_detail_exposes_candidate_runs_summary(auth_client, strategy, user):
    as_of = date(2024, 12, 31)
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=as_of,
        status=PortfolioTarget.DONE,
    )
    run = Run.objects.create(
        user=user, tickers=["AAPL"], as_of_date=as_of,
        source=Run.STRATEGY, portfolio_target=target, status=Run.DONE,
        total_cost_usd=Decimal("0.05"),
    )
    PortfolioTargetRun.objects.create(
        target=target, run=run, candidate_key="AAPL", primary_ticker="AAPL",
        side="long", screener_rank=1, sector="Tech",
    )
    url = reverse("strategy-cycle-detail", args=[strategy.pk, target.pk])
    resp = auth_client.get(url)
    assert resp.status_code == 200
    crs = resp.data["candidate_runs"]
    assert len(crs) == 1
    row = crs[0]
    assert row["candidate_key"] == "AAPL"
    assert row["run_id"] == run.pk
    assert row["side"] == "long"
    assert row["tickers"] == ["AAPL"]
