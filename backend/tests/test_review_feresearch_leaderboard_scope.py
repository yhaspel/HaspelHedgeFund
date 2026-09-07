"""Review (feresearch) — leaderboard "Flavor benchmarks" scope, now FIXED.

The finding: the Leaderboard page says *"Median across your own strategies of
each flavor (single-tenant)"* (frontend/src/app/presentation/leaderboard/
leaderboard.page.ts:428) and the API answered with ``note: "Single-tenant: each
row aggregates your own strategies of that flavor."`` — but
``recompute_strategies`` iterated ``PortfolioStrategy.objects.filter(
is_active=True)`` for EVERY user and the ``strategy=None`` flavor rows it wrote
had no owner column at all, so ``FlavorBenchmarkView`` served one global median
to every authenticated caller. A user with zero strategies saw a "Flavor
benchmarks" table computed from other users' books.

Wave 3 (WP P1): ``StrategyScorecard`` gained a ``user`` FK, flavor aggregates
are written per owner, and ``FlavorBenchmarkView`` filters on the caller. The
note is now true.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.data.models import DailyBar
from apps.leaderboard import compute
from apps.portfolios.models import Portfolio, PortfolioStrategy, PortfolioTarget, Universe

User = get_user_model()

BASE = dt.date(2026, 4, 1)


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="fl-owner@x.test", password="pw-fake-123456789")


@pytest.fixture
def stranger(db):
    return User.objects.create_user(email="fl-stranger@x.test", password="pw-fake-123456789")


def _bars(ticker, closes, start=BASE):
    for i, c in enumerate(closes):
        DailyBar.objects.get_or_create(
            ticker=ticker, date=start + dt.timedelta(days=i), source="fmp",
            defaults={
                "open": c, "high": c, "low": c, "close": c,
                "adjusted_close": c, "volume": 1000,
            },
        )


def _strategy_with_cycles(user, name: str, rets: list[str]) -> PortfolioStrategy:
    universe = Universe.objects.create(name=f"uni-{name}", is_active=True)
    portfolio = Portfolio.objects.create(
        user=user, name=f"{name}-book", kind="strategy", cash_balance=Decimal("100000"),
    )
    s = PortfolioStrategy.objects.create(
        user=user, name=name, kind=PortfolioStrategy.KIND_LONG_SHORT,
        universe=universe, portfolio=portfolio,
    )
    for i, r in enumerate(rets):
        PortfolioTarget.objects.create(
            strategy=s, as_of_date=BASE + dt.timedelta(days=i),
            status=PortfolioTarget.DONE,
            target_weights={"AAPL": 1.0, "MSFT": -0.5},
            marked_snapshot={"since_as_of_pct": r},
        )
    return s


@pytest.mark.django_db
def test_flavor_benchmarks_are_scoped_to_the_caller(owner, stranger):
    # Only `owner` has strategies (two long/short books).
    _bars("AAPL", [100, 101, 100.5, 102, 103])
    _bars("MSFT", [50, 50.5, 50.2, 49.8, 50.1])
    _strategy_with_cycles(owner, "ls-1", ["1.0", "-0.5", "2.0", "0.5"])
    _strategy_with_cycles(owner, "ls-2", ["0.2", "0.1", "-0.3", "0.4"])
    assert PortfolioStrategy.objects.filter(user=stranger).count() == 0

    client = APIClient()
    client.force_authenticate(user=stranger)
    # Any authenticated user may trigger the (global) rebuild.
    assert client.post("/api/leaderboard/recompute/").status_code == 200

    # The stranger's "My strategies" tab is honestly empty…
    mine = client.get("/api/leaderboard/strategies/?window=lifetime")
    assert mine.status_code == 200
    assert mine.json()["rows"] == []

    # …and so is "Flavor benchmarks" — the owner's books are no longer
    # aggregated into a table labelled as the stranger's own.
    flavor = client.get("/api/leaderboard/strategies/by-flavor/?window=lifetime")
    assert flavor.status_code == 200
    body = flavor.json()
    assert "your own strategies" in body["note"]
    assert body["rows"] == []

    # The owner does see their own flavor aggregate, over their own 2 books.
    owner_client = APIClient()
    owner_client.force_authenticate(user=owner)
    owned = owner_client.get(
        "/api/leaderboard/strategies/by-flavor/?window=lifetime"
    ).json()["rows"]
    rows = [r for r in owned if r["flavor"] == PortfolioStrategy.KIND_LONG_SHORT]
    assert len(rows) == 1
    assert rows[0]["n_cycles"] == 2  # "n strategies" on a flavor row
    # 4 cycles per book is far below the 20-observation bar, so the aggregate
    # is provisional and quotes no ratio.
    assert rows[0]["provisional"] is True
    assert rows[0]["sharpe"] is None


@pytest.mark.django_db
def test_flavor_rows_are_partitioned_by_user(owner, stranger):
    """The persisted aggregate now carries an owner, so a per-user view is a
    plain filter rather than an impossible schema."""
    from apps.leaderboard.models import StrategyScorecard

    assert any(f.name == "user" for f in StrategyScorecard._meta.get_fields())

    _bars("AAPL", [100, 101, 100.5, 102, 103])
    _bars("MSFT", [50, 50.5, 50.2, 49.8, 50.1])
    _strategy_with_cycles(owner, "ls-1", ["1.0", "-0.5", "2.0", "0.5"])
    _strategy_with_cycles(stranger, "ls-2", ["0.2", "0.1", "-0.3", "0.4"])
    compute.recompute_strategies(timezone.localdate())

    flavor_rows = StrategyScorecard.objects.filter(strategy__isnull=True)
    assert flavor_rows.exists()
    assert not flavor_rows.filter(user__isnull=True).exists()
    # One aggregate per (owner, flavor, window) — never one shared row.
    assert flavor_rows.filter(user=owner, window="lifetime").count() == 1
    assert flavor_rows.filter(user=stranger, window="lifetime").count() == 1
    assert (
        flavor_rows.filter(user=owner, window="lifetime").first().n_cycles == 1
    )  # one strategy each, not two
