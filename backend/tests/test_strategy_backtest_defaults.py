"""phase-09a §8.1 — GET /api/strategies/<id>/backtest-defaults/.

The endpoint returns a cheap-but-complete validation-run config derived from the
strategy: the FULL active universe (never trimmed), exactly one persona, CIO on,
monthly rebalance, and the strategy book's starting cash. Owner-scoped, read-only.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.portfolios.models import (
    Portfolio,
    PortfolioStrategy,
    Universe,
    UniverseMembership,
)
from hedgefund_agents.personas import ALL_PERSONAS

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="bd@x.test", password="pw-fake-123456789")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _strategy(user, *, tickers=("AAPL",), cash="100000", personas=None, name="S", **kw):
    """A strategy whose universe holds ``tickers`` (each active since 2020) and
    whose book carries ``cash``."""
    u = Universe.objects.create(name=f"bd-uni-{name}")
    for t in tickers:
        UniverseMembership.objects.create(
            universe=u, ticker=t, effective_from=dt.date(2020, 1, 1),
        )
    pf = Portfolio.objects.create(
        user=user, kind=Portfolio.KIND_STRATEGY, name=name, cash_balance=Decimal(cash),
    )
    return PortfolioStrategy.objects.create(
        user=user, name=name, universe=u, portfolio=pf,
        kind=PortfolioStrategy.KIND_LONG_SHORT,
        personas=personas if personas is not None else [], **kw,
    )


def _url(s):
    return f"/api/strategies/{s.id}/backtest-defaults/"


def test_universe_is_full_active_set_sorted_no_trim(client, user):
    tickers = [f"T{i:03d}" for i in range(30)]  # 30 names — well above any trim cap
    s = _strategy(user, tickers=tickers)
    r = client.get(_url(s))
    assert r.status_code == 200
    body = r.json()
    assert body["universe"] == sorted(tickers)   # full set, sorted, untrimmed
    assert len(body["universe"]) == 30


def test_universe_dedupes_multiple_active_membership_rows(client, user):
    """A ticker with two simultaneously-active membership rows (allowed —
    unique_together is (universe, ticker, effective_from)) appears once."""
    s = _strategy(user, tickers=["AAPL"])
    UniverseMembership.objects.create(
        universe=s.universe, ticker="AAPL", effective_from=dt.date(2021, 1, 1),
    )  # second active row for AAPL (different effective_from)
    body = client.get(_url(s)).json()
    assert body["universe"] == ["AAPL"]   # deduped, not ["AAPL", "AAPL"]


def test_cheap_levers_monthly_and_cio(client, user):
    s = _strategy(user)
    body = client.get(_url(s)).json()
    assert body["rebalance_frequency"] == "monthly"
    assert body["include_cio"] is True


def test_primary_persona_is_first_valid_in_strategy_order(client, user):
    # Put a registry-LATE persona first and a registry-EARLY one second, so this
    # discriminates strategy-order (correct) from registry-order (a bug): an impl
    # iterating ALL_PERSONAS would wrongly return `earlier`.
    later, earlier = ALL_PERSONAS[-1], ALL_PERSONAS[0]
    assert later != earlier
    s = _strategy(user, personas=["not_a_real_persona", later, earlier])
    body = client.get(_url(s)).json()
    assert body["personas"] == [later]           # first registry-valid id, STRATEGY order


def test_primary_persona_falls_back_to_buffett(client, user):
    # Empty list and all-invalid both fall back — never an empty list (which would
    # make the engine expand to all 8 personas).
    s_empty = _strategy(user, personas=[], name="empty")
    s_bad = _strategy(user, personas=["nope", "also_nope"], name="bad")
    assert client.get(_url(s_empty)).json()["personas"] == ["buffett"]
    assert client.get(_url(s_bad)).json()["personas"] == ["buffett"]


def test_starting_cash_reflects_strategy_book(client, user):
    s = _strategy(user, cash="250000.50")
    assert client.get(_url(s)).json()["starting_cash"] == 250000.50


def test_name_and_kind_present(client, user):
    s = _strategy(user)
    body = client.get(_url(s)).json()
    assert body["name"].startswith("Validation WF ")
    assert body["kind"] == PortfolioStrategy.KIND_LONG_SHORT
    assert body["strategy_id"] == s.id


def test_active_member_filtering_is_boundary_exact(client, user):
    """As-of-date filtering, boundary-exact (kills gt→gte / lte→lt mutants):
      - effective_to in the past        → excluded
      - effective_to == today (as_of)   → excluded (filter is effective_to__gt)
      - effective_to == tomorrow        → included (still active)
      - effective_from in the future    → excluded (not yet active)
    """
    today = timezone.localdate()
    s = _strategy(user, tickers=["AAPL"])
    uni = s.universe
    UniverseMembership.objects.create(
        universe=uni, ticker="EXPIRED",
        effective_from=dt.date(2020, 1, 1), effective_to=dt.date(2021, 1, 1),
    )
    UniverseMembership.objects.create(
        universe=uni, ticker="ENDSTODAY",
        effective_from=dt.date(2020, 1, 1), effective_to=today,
    )
    UniverseMembership.objects.create(
        universe=uni, ticker="ENDSTOMORROW",
        effective_from=dt.date(2020, 1, 1), effective_to=today + dt.timedelta(days=1),
    )
    UniverseMembership.objects.create(
        universe=uni, ticker="FUTURE", effective_from=today + dt.timedelta(days=30),
    )
    body = client.get(_url(s)).json()
    assert body["universe"] == ["AAPL", "ENDSTOMORROW"]


def test_empty_universe_returns_empty_list(client, user):
    s = _strategy(user, tickers=[])
    assert client.get(_url(s)).json()["universe"] == []


def test_other_users_strategy_is_404(client, user):
    other = User.objects.create_user(email="bd-other@x.test", password="pw-fake-123456789")
    s = _strategy(other)
    r = client.get(_url(s))
    assert r.status_code == 404            # no existence leak


def test_unauthenticated_is_rejected(user):
    s = _strategy(user)
    r = APIClient().get(_url(s))           # no force_authenticate
    assert r.status_code in (401, 403)
