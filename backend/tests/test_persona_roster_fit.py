"""P7c Part E — persona roster-by-fit recommendations + the non-blocking guardrail
that encodes the research lesson (value personas abstain on ETFs)."""
from __future__ import annotations

import pytest

from apps.portfolios.models import PortfolioStrategy
from apps.portfolios.persona_fit import recommended_personas, roster_fit_warnings

pytestmark = pytest.mark.django_db

_ALL = {"buffett", "munger", "graham", "wood", "druckenmiller", "burry", "damodaran", "lynch"}


def test_recommended_personas_by_fit():
    # deterministic kinds → council-free (no personas)
    for k in (PortfolioStrategy.KIND_RISK_PARITY, PortfolioStrategy.KIND_TREND,
              PortfolioStrategy.KIND_SECTOR_MOMENTUM, PortfolioStrategy.KIND_PAIRS):
        assert recommended_personas(k) == []
    # ETF/macro council kinds → macro personas only
    assert recommended_personas(PortfolioStrategy.KIND_GLOBAL_MACRO) == ["druckenmiller"]
    assert recommended_personas(PortfolioStrategy.KIND_SECTOR_ROTATION) == ["druckenmiller"]
    # single-name equity → every style fits
    assert set(recommended_personas(PortfolioStrategy.KIND_LONG_SHORT)) == _ALL
    assert set(recommended_personas(PortfolioStrategy.KIND_CONCENTRATED_LONG)) == _ALL


def test_warns_on_value_personas_on_etf_kind():
    w = roster_fit_warnings(
        PortfolioStrategy.KIND_GLOBAL_MACRO, ["buffett", "graham", "druckenmiller"]
    )
    assert len(w) == 1
    assert "buffett" in w[0] and "graham" in w[0]
    assert "druckenmiller" not in w[0].split("Prefer")[0]   # the macro persona is not flagged


def test_warns_on_personas_for_deterministic_kind():
    w = roster_fit_warnings(PortfolioStrategy.KIND_TREND, ["druckenmiller"])
    assert len(w) == 1 and "deterministic" in w[0]


def test_no_warning_for_fitting_rosters():
    assert roster_fit_warnings(PortfolioStrategy.KIND_GLOBAL_MACRO, ["druckenmiller"]) == []
    assert roster_fit_warnings(PortfolioStrategy.KIND_LONG_SHORT, ["buffett", "graham"]) == []
    assert roster_fit_warnings(PortfolioStrategy.KIND_RISK_PARITY, []) == []
    assert roster_fit_warnings(PortfolioStrategy.KIND_RISK_PARITY, None) == []


def test_serializer_exposes_roster_warnings():
    from django.contrib.auth import get_user_model

    from apps.portfolios.models import Portfolio, Universe
    from apps.portfolios.serializers import StrategySerializer

    user = get_user_model().objects.create_user(email="rf@x.test", password="pw-fake-12345")
    uni = Universe.objects.create(name="rf-uni")
    pf = Portfolio.objects.create(
        user=user, kind=Portfolio.KIND_STRATEGY, name="rf", cash_balance=0
    )
    s = PortfolioStrategy.objects.create(
        user=user, name="m", kind=PortfolioStrategy.KIND_GLOBAL_MACRO,
        universe=uni, portfolio=pf, personas=["buffett", "druckenmiller"],
    )
    data = StrategySerializer(s).data
    assert any("buffett" in w for w in data["roster_warnings"])
