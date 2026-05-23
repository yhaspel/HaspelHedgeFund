"""WS-C/WS-G: agent integration and resolver tests for the investor profile."""
from __future__ import annotations

import datetime as dt
import importlib
import pkgutil

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.investor_profile.models import (
    InvestorProfileState,
    QuestionnaireResponse,
)
from apps.portfolios.models import (
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    Universe,
)
from apps.runs.models import Run
from apps.runs.tasks import _resolve_investor_profile
from hedgefund_agents.investor_profile_block import (
    CIO_FRAMING,
    PERSONA_FRAMING,
    RISK_MANAGER_FRAMING,
    format_profile_block,
)

User = get_user_model()


def _active_profile(user, brief="Cautious, long-horizon investor."):
    return QuestionnaireResponse.objects.create(
        user=user,
        source=QuestionnaireResponse.SOURCE_QUESTIONNAIRE,
        schema_version=1,
        answers={},
        model_id="openrouter:meta-llama/llama-3.3-70b-instruct",
        analysis_status=QuestionnaireResponse.DONE,
        analyzed_at=timezone.now(),
        analysis={
            "investor_type": "Patient Investor",
            "risk_band": "moderate",
            "horizon_band": "long",
            "patience_band": "high",
        },
        agent_brief=brief,
    )


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="rp@example.com", password="x", is_active=True
    )


def _mk_run(user, source=Run.ADHOC, **extra):
    return Run.objects.create(
        user=user,
        tickers=["AAPL"],
        as_of_date=dt.date.today(),
        source=source,
        **extra,
    )


# ---------- _resolve_investor_profile matrix ----------


def test_resolve_no_profile_returns_reason(user):
    run = _mk_run(user)
    ctx, meta = _resolve_investor_profile(run)
    assert ctx == {}
    assert meta["applied"] is False
    assert meta["reason"] == "no_profile"


def test_resolve_apply_to_runs_off(user):
    _active_profile(user)
    InvestorProfileState.objects.create(user=user, apply_to_runs=False)
    ctx, meta = _resolve_investor_profile(_mk_run(user))
    assert ctx == {}
    assert meta["reason"] == "personalization_off"


def test_resolve_adhoc_eligible(user):
    qr = _active_profile(user, brief="Profile A.")
    InvestorProfileState.objects.create(user=user, apply_to_runs=True)
    ctx, meta = _resolve_investor_profile(_mk_run(user))
    assert meta["applied"] is True
    assert meta["reason"] == ""
    assert meta["response_id"] == qr.id
    assert ctx["agent_brief"] == "Profile A."
    assert ctx["investor_type"] == "Patient Investor"


def test_resolve_strategy_opt_out_blocks(user):
    _active_profile(user)
    universe = Universe.objects.create(name="u1")
    portfolio = Portfolio.objects.create(user=user, name="P1")
    strategy = PortfolioStrategy.objects.create(
        user=user, name="S1", universe=universe, portfolio=portfolio,
        apply_investor_profile=False,
    )
    target = PortfolioTarget.objects.create(strategy=strategy, as_of_date=dt.date.today())
    run = _mk_run(user, source=Run.STRATEGY, portfolio_target=target)
    ctx, meta = _resolve_investor_profile(run)
    assert ctx == {}
    assert meta["reason"] == "strategy_opt_out"


def test_resolve_strategy_opt_in_personalizes(user):
    qr = _active_profile(user, brief="Opt-in profile.")
    universe = Universe.objects.create(name="u2")
    portfolio = Portfolio.objects.create(user=user, name="P2")
    strategy = PortfolioStrategy.objects.create(
        user=user, name="S2", universe=universe, portfolio=portfolio,
        apply_investor_profile=True,
    )
    target = PortfolioTarget.objects.create(strategy=strategy, as_of_date=dt.date.today())
    run = _mk_run(user, source=Run.STRATEGY, portfolio_target=target)
    ctx, meta = _resolve_investor_profile(run)
    assert meta["applied"] is True
    assert ctx["agent_brief"] == "Opt-in profile."
    assert meta["response_id"] == qr.id


# ---------- format_profile_block ----------


def test_format_block_empty_when_no_profile():
    assert format_profile_block(None, CIO_FRAMING) == ""
    assert format_profile_block({}, PERSONA_FRAMING) == ""
    assert format_profile_block({"agent_brief": ""}, RISK_MANAGER_FRAMING) == ""


def test_format_block_includes_delimiters_and_framing():
    block = format_profile_block({"agent_brief": "Test brief."}, CIO_FRAMING)
    assert CIO_FRAMING in block
    assert "<<<PROFILE>>>" in block
    assert "<<<END PROFILE>>>" in block
    assert "Test brief." in block


# ---------- backtest exclusion (architectural guard) ----------


def test_apps_investor_profile_not_imported_by_backtests():
    """No file under ``apps.backtests`` may import ``apps.investor_profile``.

    The injection happens ONLY inside ``apps.runs.tasks.execute_run`` per
    plan §3. Backtests must always carry an unset ``investor_profile`` key
    on ``AgentState``.
    """
    import apps.backtests as backtests_pkg

    forbidden = "apps.investor_profile"

    bad: list[str] = []
    for info in pkgutil.walk_packages(backtests_pkg.__path__, prefix="apps.backtests."):
        # Tests under backtests/tests are allowed to import the app for assertions.
        if ".tests." in info.name or info.name.endswith(".tests"):
            continue
        module = importlib.import_module(info.name)
        src = getattr(module, "__file__", None)
        if not src:
            continue
        with open(src, encoding="utf-8") as fh:
            content = fh.read()
        if forbidden in content:
            bad.append(f"{info.name} imports {forbidden}")
    assert not bad, "\n".join(bad)
