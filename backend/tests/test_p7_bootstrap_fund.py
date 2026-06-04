"""P7 Stage A — bootstrap_autonomous_fund: 3 accounts + 3 strategies + 3 links +
3 disabled autopilots + 1 fund; idempotent; distinct-NAME enforced."""
from __future__ import annotations

import datetime as dt

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.brokers.models import BrokerAccount, StrategyBrokerLink
from apps.portfolios.models import (
    AutonomousFund,
    PortfolioStrategy,
    StrategyAutopilot,
    Universe,
    UniverseMembership,
)

User = get_user_model()


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="fundowner@x.test", password="pw-fake-123456789")


@pytest.fixture
def universes(db):
    # The three seed universes may already exist (migration-seeded); ensure they
    # exist with at least one member either way.
    for slug, tickers in (
        ("sp500_top_200", ["AAPL", "MSFT", "NVDA"]),
        ("sector_etfs", ["XLK", "XLF", "XLE"]),
        ("macro_etfs", ["SPY", "TLT", "GLD"]),
    ):
        u, _ = Universe.objects.get_or_create(name=slug)
        if not UniverseMembership.objects.filter(universe=u).exists():
            for t in tickers:
                UniverseMembership.objects.create(
                    universe=u, ticker=t, effective_from=dt.date(2020, 1, 1),
                )


def _triples():
    return [
        {"slot": 1, "name": "multifactor-ls", "key_id": "PK1", "secret": "S1"},
        {"slot": 2, "name": "sector-rotation", "key_id": "PK2", "secret": "S2"},
        {"slot": 3, "name": "trend-cta", "key_id": "PK3", "secret": "S3"},
    ]


def _run(owner, settings, triples=None):
    settings.ALPACA_PAPER_ACCOUNTS = triples if triples is not None else _triples()
    settings.ALPACA_FUND_OWNER_EMAIL = owner.email
    call_command("bootstrap_autonomous_fund", broker="mock", no_verify=True)


def test_bootstrap_creates_full_fund(owner, universes, settings):
    _run(owner, settings)

    accts = BrokerAccount.objects.filter(user=owner, broker="mock")
    assert accts.count() == 3
    assert set(accts.values_list("label", flat=True)) == {
        "multifactor-ls", "sector-rotation", "trend-cta",
    }
    assert all(a.mode == BrokerAccount.MODE_PAPER for a in accts)
    assert all(a.connection_status == BrokerAccount.STATUS_ACTIVE for a in accts)  # demo

    strategies = PortfolioStrategy.objects.filter(user=owner)
    assert strategies.count() == 3
    kinds = set(strategies.values_list("kind", flat=True))
    assert kinds == {
        PortfolioStrategy.KIND_LONG_SHORT,
        PortfolioStrategy.KIND_SECTOR_ROTATION,
        PortfolioStrategy.KIND_GLOBAL_MACRO,
    }
    assert all(s.auto_run_council for s in strategies)            # §6.0 — required

    assert StrategyBrokerLink.objects.filter(is_active=True).count() == 3
    aps = StrategyAutopilot.objects.all()
    assert aps.count() == 3
    assert all(not a.is_enabled for a in aps)                     # disabled until §9 gate passes
    assert {a.cron_expression for a in aps} == {"30 16 * * 5", "45 16 * * 5", "0 17 * * 5"}

    fund = AutonomousFund.objects.get(owner=owner)
    assert fund.strategies.count() == 3


def test_bootstrap_is_idempotent(owner, universes, settings):
    _run(owner, settings)
    _run(owner, settings)  # re-run
    assert BrokerAccount.objects.filter(user=owner, broker="mock").count() == 3
    assert PortfolioStrategy.objects.filter(user=owner).count() == 3
    assert StrategyBrokerLink.objects.filter(is_active=True).count() == 3
    assert StrategyAutopilot.objects.count() == 3
    assert AutonomousFund.objects.filter(owner=owner).count() == 1


def test_bootstrap_rejects_duplicate_names(owner, universes, settings):
    dup = _triples()
    dup[1]["name"] = "multifactor-ls"  # collide with slot 1
    with pytest.raises(CommandError, match="duplicate NAME"):
        _run(owner, settings, triples=dup)


def test_bootstrap_reports_partial_triple(owner, universes, settings):
    partial = _triples()
    partial[2]["secret"] = ""  # incomplete slot 3
    _run(owner, settings, triples=partial)
    # Only the two complete slots are provisioned; the partial one is skipped.
    assert BrokerAccount.objects.filter(user=owner, broker="mock").count() == 2
    assert PortfolioStrategy.objects.filter(user=owner).count() == 2


def test_bootstrap_requires_owner(owner, universes, settings):
    settings.ALPACA_PAPER_ACCOUNTS = _triples()
    settings.ALPACA_FUND_OWNER_EMAIL = ""
    with pytest.raises(CommandError, match="no owner"):
        call_command("bootstrap_autonomous_fund", broker="mock", no_verify=True)
