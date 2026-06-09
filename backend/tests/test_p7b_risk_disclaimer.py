"""P7b §G — PortfolioStrategy.risk_disclaimer is surfaced on the strategy API."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from apps.portfolios.models import Portfolio, PortfolioStrategy, Universe
from apps.portfolios.serializers import StrategySerializer

User = get_user_model()
pytestmark = pytest.mark.django_db


def _strategy(user, **kw):
    u = Universe.objects.create(name="rd-uni")
    pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_STRATEGY, name="b")
    return PortfolioStrategy.objects.create(
        user=user, name="S", universe=u, portfolio=pf,
        kind=PortfolioStrategy.KIND_RISK_PARITY, personas=[], **kw,
    )


def test_risk_disclaimer_defaults_blank():
    u = User.objects.create_user(email="rd1@x.test", password="pw-fake-123456789")
    s = _strategy(u)
    assert s.risk_disclaimer == ""
    assert StrategySerializer(s).data["risk_disclaimer"] == ""


def test_risk_disclaimer_round_trips_through_serializer():
    u = User.objects.create_user(email="rd2@x.test", password="pw-fake-123456789")
    caveat = "Bear-resistant, not bear-proof."
    s = _strategy(u, risk_disclaimer=caveat)
    assert StrategySerializer(s).data["risk_disclaimer"] == caveat
