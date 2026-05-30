"""API + service tests for the investor-profile app (P3-prereq-5 WS-A/B/E/F)."""
from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.investor_profile import analysis as analysis_mod
from apps.investor_profile.analysis import (
    ProfileAnalysis,
    StrategyRecommendation,
    build_fallback_brief,
    build_fallback_strategies,
)
from apps.investor_profile.models import (
    InvestorProfileState,
    QuestionnaireResponse,
    compute_nudge,
)
from apps.investor_profile.questionnaire import (
    SCHEMA_VERSION,
    QuestionnaireValidationError,
    validate_answers,
)
from apps.investor_profile.tasks import run_profile_analysis
from apps.investor_profile.tune import rederive_for_tune
from apps.watchlists.models import Watchlist, WatchlistTicker
from apps.watchlists.services import add_tickers_to_watchlist

User = get_user_model()


def _valid_answers(**overrides):
    a = {
        "age_band": "35–44",
        "experience": "Some (1–5 yrs)",
        "primary_goal": "Balanced growth",
        "time_horizon": "7–15 yrs",
        "risk_self_rating": "Moderate",
        "drawdown_reaction": "Hold and wait",
        "max_annual_loss": "Up to 25%",
        "patience": "1–2 years",
        "trade_frequency": "A few times a year",
        "decision_style": "Numbers & data",
        "concentration": "Somewhere in between",
        "favorite_tickers": ["AAPL", "msft", "googl"],
        "preferred_sectors": ["Technology"],
    }
    a.update(overrides)
    return a


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="ip-test@example.com", password="x", is_active=True
    )


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _stub_analysis(brief="Stub brief.") -> ProfileAnalysis:
    return ProfileAnalysis(
        investor_type="Balanced Growth Investor",
        risk_band="moderate",
        horizon_band="long",
        patience_band="medium",
        behavioral_traits=["data-driven", "patient"],
        key_constraints=["max drawdown 25%"],
        summary=(
            "You're a balanced-growth investor with a long horizon and moderate "
            "risk appetite — calibrated to data, patient with theses, "
            "comfortable with multi-year drawdowns within reason."
        ),
        insights=[
            "Diversification keeps single-name shocks contained.",
            "Quarterly check-ins suit your trading cadence.",
        ],
        recommended_strategies=[
            StrategyRecommendation(
                kind="long_only", fit="strong", rationale="Matches your patience."
            ),
            StrategyRecommendation(
                kind="sector_rotation", fit="good", rationale="Theme exposure."
            ),
        ],
        agent_brief=brief,
    )


# ---------- validator ----------


def test_validate_answers_happy_path():
    ans = _valid_answers()
    normalized = validate_answers(ans)
    assert normalized["favorite_tickers"] == ["AAPL", "MSFT", "GOOGL"]
    assert normalized["risk_self_rating"] == "Moderate"


def test_validate_answers_missing_required():
    ans = _valid_answers()
    del ans["age_band"]
    with pytest.raises(QuestionnaireValidationError):
        validate_answers(ans)


def test_validate_answers_invalid_option():
    ans = _valid_answers(risk_self_rating="Mostly fine")
    with pytest.raises(QuestionnaireValidationError):
        validate_answers(ans)


def test_validate_answers_text_too_long():
    ans = _valid_answers(avoid_notes="x" * 201)
    with pytest.raises(QuestionnaireValidationError):
        validate_answers(ans)


def test_validate_answers_bad_ticker():
    ans = _valid_answers(favorite_tickers=["AAPL", "@@bad"])
    with pytest.raises(QuestionnaireValidationError):
        validate_answers(ans)


def test_validate_answers_unknown_schema_version():
    with pytest.raises(QuestionnaireValidationError):
        validate_answers(_valid_answers(), schema_version=999)


# ---------- compute_nudge ----------


def test_compute_nudge_no_state_returns_modal():
    n = compute_nudge(state=None, has_done_questionnaire=False)
    assert n == {"due": True, "form": "modal"}


def test_compute_nudge_done_questionnaire_silent():
    n = compute_nudge(state=None, has_done_questionnaire=True)
    assert n["due"] is False


def test_compute_nudge_banner_after_first_dismissal(db, user):
    state = InvestorProfileState.objects.create(
        user=user,
        nudge_dismiss_count=1,
        nudge_last_dismissed_at=timezone.now() - dt.timedelta(days=31),
    )
    n = compute_nudge(state, has_done_questionnaire=False)
    assert n == {"due": True, "form": "banner"}


def test_compute_nudge_grace_period(db, user):
    state = InvestorProfileState.objects.create(
        user=user,
        nudge_dismiss_count=1,
        nudge_last_dismissed_at=timezone.now() - dt.timedelta(days=5),
    )
    n = compute_nudge(state, has_done_questionnaire=False)
    assert n["due"] is False


# ---------- profile bundle ----------


def test_profile_bundle_empty(client, user):
    resp = client.get("/api/profile/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_questionnaire"] is False
    assert body["active"] is None
    assert body["nudge"]["form"] == "modal"


def test_questionnaire_schema_endpoint(client):
    resp = client.get("/api/profile/questionnaire/schema/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == SCHEMA_VERSION
    assert {s["id"] for s in body["sections"]} == {
        "about",
        "goals",
        "risk",
        "temperament",
        "preferences",
    }


# ---------- submit + analysis ----------


def test_submit_invalid_400_before_llm(client):
    bad = _valid_answers()
    del bad["age_band"]
    resp = client.post(
        "/api/profile/questionnaire/",
        {"answers": bad, "model_id": "openrouter:meta-llama/llama-3.3-70b-instruct"},
        format="json",
    )
    assert resp.status_code == 400


def test_submit_unknown_model_400(client):
    resp = client.post(
        "/api/profile/questionnaire/",
        {"answers": _valid_answers(), "model_id": "openai:gpt-bad"},
        format="json",
    )
    assert resp.status_code == 400


def test_submit_happy_path_runs_analysis_and_seeds_watchlist(client, user):
    with patch(
        "apps.investor_profile.tasks.analyze_questionnaire",
        return_value=_stub_analysis(),
    ):
        resp = client.post(
            "/api/profile/questionnaire/",
            {
                "answers": _valid_answers(),
                "model_id": "openrouter:meta-llama/llama-3.3-70b-instruct",
            },
            format="json",
        )
    assert resp.status_code == 201, resp.json()
    body = resp.json()
    assert body["analysis_status"] == "done"
    assert body["analysis"]["investor_type"] == "Balanced Growth Investor"
    assert body["agent_brief"] == "Stub brief."
    # auto-seed merged favorites into the user's default watchlist
    wl = Watchlist.objects.get(user=user)
    tickers = sorted(wl.tickers.values_list("ticker", flat=True))
    assert tickers == ["AAPL", "GOOGL", "MSFT"]


def test_submit_429_cost_guard(client, user):
    for _ in range(10):
        QuestionnaireResponse.objects.create(
            user=user,
            source=QuestionnaireResponse.SOURCE_QUESTIONNAIRE,
            schema_version=SCHEMA_VERSION,
            answers={},
            model_id="openrouter:meta-llama/llama-3.3-70b-instruct",
            analysis_status=QuestionnaireResponse.DONE,
        )
    resp = client.post(
        "/api/profile/questionnaire/",
        {"answers": _valid_answers()},
        format="json",
    )
    assert resp.status_code == 429


def test_questionnaire_detail_404_cross_user(db, client, user):
    other = User.objects.create_user(email="other@example.com", password="x")
    row = QuestionnaireResponse.objects.create(
        user=other,
        source=QuestionnaireResponse.SOURCE_QUESTIONNAIRE,
        schema_version=SCHEMA_VERSION,
        answers={},
        model_id="openrouter:meta-llama/llama-3.3-70b-instruct",
        analysis_status=QuestionnaireResponse.DONE,
    )
    resp = client.get(f"/api/profile/questionnaire/{row.id}/")
    assert resp.status_code == 404


# ---------- run_profile_analysis task ----------


def test_run_profile_analysis_failure_marks_failed(db, user):
    row = QuestionnaireResponse.objects.create(
        user=user,
        source=QuestionnaireResponse.SOURCE_QUESTIONNAIRE,
        schema_version=SCHEMA_VERSION,
        answers=_valid_answers(),
        model_id="openrouter:meta-llama/llama-3.3-70b-instruct",
        analysis_status=QuestionnaireResponse.PENDING,
    )
    with patch(
        "apps.investor_profile.tasks.analyze_questionnaire",
        side_effect=RuntimeError("boom"),
    ):
        result = run_profile_analysis(row.id)
    assert result["status"] == "failed"
    row.refresh_from_db()
    assert row.analysis_status == QuestionnaireResponse.FAILED
    assert "boom" in row.error_message


# ---------- tune ----------


def test_tune_creates_new_done_row_no_llm(client, user):
    parent = QuestionnaireResponse.objects.create(
        user=user,
        source=QuestionnaireResponse.SOURCE_QUESTIONNAIRE,
        schema_version=SCHEMA_VERSION,
        answers=_valid_answers(),
        model_id="openrouter:meta-llama/llama-3.3-70b-instruct",
        analysis_status=QuestionnaireResponse.DONE,
        analysis=_stub_analysis().model_dump(),
        agent_brief="Parent brief.",
    )
    resp = client.post(
        f"/api/profile/questionnaire/{parent.id}/tune/",
        {"risk_band": "aggressive"},
        format="json",
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["source"] == "tuned"
    assert body["analysis_status"] == "done"
    assert body["analysis"]["risk_band"] == "aggressive"
    assert body["model_id"] == "(manual tune)"
    # Parent row must be untouched.
    parent.refresh_from_db()
    assert parent.analysis["risk_band"] == "moderate"


def test_tune_rejects_invalid_band(client, user):
    parent = QuestionnaireResponse.objects.create(
        user=user,
        source=QuestionnaireResponse.SOURCE_QUESTIONNAIRE,
        schema_version=SCHEMA_VERSION,
        answers=_valid_answers(),
        model_id="openrouter:meta-llama/llama-3.3-70b-instruct",
        analysis_status=QuestionnaireResponse.DONE,
        analysis=_stub_analysis().model_dump(),
    )
    resp = client.post(
        f"/api/profile/questionnaire/{parent.id}/tune/",
        {"risk_band": "yolo"},
        format="json",
    )
    assert resp.status_code == 400


def test_rederive_for_tune_strategies_change_with_risk():
    parent_analysis = _stub_analysis().model_dump()
    out = rederive_for_tune(
        parent_analysis=parent_analysis,
        parent_answers=_valid_answers(primary_goal="Preserve capital"),
        risk_band="conservative",
    )
    assert out.risk_band == "conservative"
    kinds = [r.kind for r in out.recommended_strategies]
    assert "risk_parity" in kinds


# ---------- state + nudge endpoints ----------


def test_state_patch(client, user):
    resp = client.patch(
        "/api/profile/state/", {"apply_to_runs": False}, format="json"
    )
    assert resp.status_code == 200
    assert resp.json()["apply_to_runs"] is False
    state = InvestorProfileState.objects.get(user=user)
    assert state.apply_to_runs is False


def test_nudge_dismiss(client, user):
    resp = client.post("/api/profile/nudge/dismiss/")
    assert resp.status_code == 200
    state = InvestorProfileState.objects.get(user=user)
    assert state.nudge_dismiss_count == 1
    assert state.nudge_last_dismissed_at is not None


# ---------- fallbacks ----------


def test_fallback_brief_non_empty():
    text = build_fallback_brief(_valid_answers())
    assert "risk appetite" in text
    assert "time horizon" in text


def test_fallback_strategies_only_real_kinds():
    out = build_fallback_strategies(_valid_answers(), risk_band="moderate")
    for s in out:
        assert s.kind in analysis_mod.STRATEGY_KINDS


# ---------- add_tickers_to_watchlist (WS-E helper) ----------


def test_add_tickers_to_watchlist_idempotent(db, user):
    first = add_tickers_to_watchlist(user, ["AAPL", "msft", "AAPL"])
    second = add_tickers_to_watchlist(user, ["aapl", "MSFT", "TSLA"])
    assert first == 2
    assert second == 1
    wl = Watchlist.objects.get(user=user)
    assert sorted(wl.tickers.values_list("ticker", flat=True)) == [
        "AAPL",
        "MSFT",
        "TSLA",
    ]


def test_add_tickers_respects_cap(db, user):
    wl = Watchlist.objects.create(user=user, is_default=True)
    WatchlistTicker.objects.bulk_create(
        [WatchlistTicker(watchlist=wl, ticker=f"T{i:03d}") for i in range(100)]
    )
    added = add_tickers_to_watchlist(user, ["NEW1", "NEW2"])
    assert added == 0


def test_add_tickers_empty_input(db, user):
    assert add_tickers_to_watchlist(user, []) == 0
    assert add_tickers_to_watchlist(user, None) == 0  # type: ignore[arg-type]
