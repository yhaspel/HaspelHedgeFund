"""P3 addendum: per-cycle marked snapshot."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from apps.data.models import DailyBar
from apps.portfolios.cycle_mark import (
    compute_cycle_snapshot,
    ensure_cycle_snapshot,
)
from apps.portfolios.models import (
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    Universe,
)

User = get_user_model()


def _seed_close(ticker: str, on: date, close: float) -> None:
    DailyBar.objects.update_or_create(
        ticker=ticker, date=on, source="fmp",
        defaults={
            "open": close, "high": close, "low": close, "close": close,
            "adjusted_close": close, "volume": 1_000_000,
        },
    )


class _StubProvider:
    def get_daily_bars(self, ticker, start, end, *, as_of):
        from apps.data.interfaces import Bar
        rows = DailyBar.objects.filter(
            ticker=ticker, source="fmp",
            date__gte=start, date__lte=min(end, as_of),
        ).order_by("date")
        return [
            Bar(
                ticker=r.ticker, date=r.date,
                open=r.open, high=r.high, low=r.low, close=r.close,
                adjusted_close=r.adjusted_close, volume=r.volume,
            )
            for r in rows
        ]


@pytest.fixture(autouse=True)
def _stub_fmp():
    with patch("apps.portfolios.cycle_mark.get_fmp_provider") as m:
        m.return_value = _StubProvider()
        yield m


@pytest.fixture
def user(db):
    return User.objects.create_user(email="cm@example.com", password="x")


def _strategy(user) -> PortfolioStrategy:
    universe = Universe.objects.create(name="test-cm", is_active=True)
    portfolio = Portfolio.objects.create(
        user=user, name="strat", kind="strategy", cash_balance=Decimal("100000"),
    )
    return PortfolioStrategy.objects.create(
        user=user, name="s", universe=universe, portfolio=portfolio,
    )


def test_empty_target_returns_trivial_snapshot(db, user):
    strategy = _strategy(user)
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date.today() - timedelta(days=7),
        status="done", target_weights={},
    )
    snap = compute_cycle_snapshot(target)
    assert snap["since_as_of_pct"] == "0.00"
    assert snap["per_ticker"] == {}
    assert any("Empty target book" in w for w in snap["warnings"])


def test_long_short_book_marks_correctly(db, user):
    strategy = _strategy(user)
    as_of = date.today() - timedelta(days=7)
    today = date.today()
    # Long: +3% weight on AAPL that rose 10% → contributes +0.3pp.
    # Short: -1% weight on GME that fell 5% → contributes +0.05pp (short profits).
    _seed_close("AAPL", as_of, 100)
    _seed_close("AAPL", today, 110)
    _seed_close("GME", as_of, 50)
    _seed_close("GME", today, 47.5)

    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=as_of, status="done",
        target_weights={"AAPL": 0.03, "GME": -0.01},
    )
    snap = compute_cycle_snapshot(target)

    aapl = snap["per_ticker"]["AAPL"]
    assert aapl["as_of_price"] == "100.0000"
    assert aapl["mark_price"] == "110.0000"
    assert aapl["return_pct"] == "10.00"
    assert aapl["contribution_pp"] == "0.30"
    assert aapl["weight_pct"] == "3.00"

    gme = snap["per_ticker"]["GME"]
    assert gme["return_pct"] == "-5.00"
    # weight -1%, return -5% → contribution = -1 * -5 / 100 = +0.05pp
    assert gme["contribution_pp"] == "0.05"

    # Total: +0.30 + 0.05 = +0.35pp
    assert snap["since_as_of_pct"] == "0.35"


def test_missing_close_records_warning(db, user):
    strategy = _strategy(user)
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=date.today() - timedelta(days=7),
        status="done", target_weights={"NOSUCH": 0.02},
    )
    snap = compute_cycle_snapshot(target)
    row = snap["per_ticker"]["NOSUCH"]
    assert row["as_of_price"] is None
    assert row["mark_price"] is None
    assert "no close on or before cycle as_of_date" in row["warnings"]
    # contribution defaults to zero when prices are unavailable.
    assert row["contribution_pp"] == "0.00"


def test_ensure_snapshot_persists_and_caches(db, user):
    strategy = _strategy(user)
    as_of = date.today() - timedelta(days=7)
    _seed_close("AAPL", as_of, 100)
    _seed_close("AAPL", date.today(), 110)
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=as_of, status="done",
        target_weights={"AAPL": 0.03},
    )
    first = ensure_cycle_snapshot(target)
    target.refresh_from_db()
    assert target.marked_snapshot == first
    second = ensure_cycle_snapshot(target)
    # Same stamp — cache hit returns the persisted snapshot unchanged.
    assert second["snapshot_at"] == first["snapshot_at"]


def test_ensure_snapshot_force_recomputes(db, user):
    strategy = _strategy(user)
    as_of = date.today() - timedelta(days=7)
    _seed_close("AAPL", as_of, 100)
    _seed_close("AAPL", date.today(), 110)
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=as_of, status="done",
        target_weights={"AAPL": 0.03},
    )
    first = ensure_cycle_snapshot(target)
    # Bump the close so a forced recompute picks up the new value.
    _seed_close("AAPL", date.today(), 120)
    second = ensure_cycle_snapshot(target, force=True)
    assert first["per_ticker"]["AAPL"]["mark_price"] == "110.0000"
    assert second["per_ticker"]["AAPL"]["mark_price"] == "120.0000"
    assert second["since_as_of_pct"] == "0.60"  # 20% * 3% = 0.60pp
