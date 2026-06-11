"""P10 §C2/§C3 — persisted NAV history + time-weighted returns.

The hourly guardrail_sweep used to compute each account's marked equity and
throw it away; now it upserts PortfolioSnapshot rows. GET /api/fund/history/
serves per-account + aggregate series with TWR (flow-adjusted) indices and
SPY/QQQ overlays. The Alpaca backfill command seeds history from the broker.
"""
from __future__ import annotations

import datetime as dt
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.brokers.models import BrokerAccount, StrategyBrokerLink
from apps.data.models import DailyBar
from apps.portfolios.models import (
    AutonomousFund,
    LedgerEntry,
    Portfolio,
    PortfolioSnapshot,
    PortfolioStrategy,
    StrategyAutopilot,
    Universe,
    UniverseMembership,
)
from apps.portfolios.snapshots import (
    external_flow,
    record_snapshot,
    twr_index,
    twr_returns,
)

User = get_user_model()

D0 = dt.date(2026, 6, 1)


@pytest.fixture
def user(db):
    return User.objects.create_user(email="p10c@x.test", password="pw-fake-123456789")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _strategy(user, name="S"):
    u = Universe.objects.create(name=f"p10c-uni-{name}")
    UniverseMembership.objects.create(universe=u, ticker="AAA", effective_from=D0)
    pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_STRATEGY, name=name)
    return PortfolioStrategy.objects.create(
        user=user, name=name, universe=u, portfolio=pf,
        kind=PortfolioStrategy.KIND_RISK_PARITY,
    )


def _account(user, label="A", cash="100000"):
    pf = Portfolio.objects.create(
        user=user, kind=Portfolio.KIND_BROKER, name=f"bk-{label}",
        cash_balance=Decimal(cash),
    )
    return BrokerAccount.objects.create(
        user=user, broker="mock", mode=BrokerAccount.MODE_PAPER,
        account_id=f"mock-{label}", label=label, portfolio=pf,
        connection_status=BrokerAccount.STATUS_ACTIVE,
    )


def _fund_of(user, n=2):
    fund = AutonomousFund.objects.create(owner=user, name="Fund")
    out = []
    for i in range(n):
        s = _strategy(user, name=f"S{i}")
        acc = _account(user, label=f"A{i}")
        StrategyBrokerLink.objects.create(strategy=s, broker_account=acc)
        StrategyAutopilot.objects.create(strategy=s, broker_account=acc, is_enabled=True)
        fund.strategies.add(s)
        out.append((s, acc))
    return fund, out


def _snap(pf, on, equity, flow="0"):
    record_snapshot(pf, equity=equity, on=on, net_flow=flow)


# ---------------------------------------------------------------------------
# record_snapshot / external_flow.
# ---------------------------------------------------------------------------
def test_record_snapshot_upserts_per_day(db, user):
    pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_BROKER, name="b")
    record_snapshot(pf, equity="100000", on=D0, net_flow="0")
    record_snapshot(pf, equity="100500.55", on=D0, net_flow="0")  # later sweep wins
    rows = PortfolioSnapshot.objects.filter(portfolio=pf)
    assert rows.count() == 1
    assert rows.first().equity == Decimal("100500.55")


def test_external_flow_reads_ledger_kinds(db, user):
    pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_BROKER, name="b")
    LedgerEntry.objects.create(
        portfolio=pf, kind=LedgerEntry.KIND_DEPOSIT, cash_delta=Decimal("25000"),
        cash_balance_after=Decimal("125000"),
    )
    LedgerEntry.objects.create(
        portfolio=pf, kind=LedgerEntry.KIND_BROKER_FILL, cash_delta=Decimal("-5000"),
        cash_balance_after=Decimal("120000"),
    )  # trading — NOT an external flow
    today = dt.date.today()
    assert external_flow(pf, today) == Decimal("25000")


# ---------------------------------------------------------------------------
# TWR math — the heart of §C2: flows never print as performance.
# ---------------------------------------------------------------------------
def test_twr_ignores_deposit():
    pts = [
        {"date": D0, "equity": 100_000.0, "net_flow": 0.0},
        # Doubled by a $100K deposit, NOT by performance:
        {"date": D0 + dt.timedelta(days=1), "equity": 200_000.0, "net_flow": 100_000.0},
    ]
    assert twr_returns(pts) == [pytest.approx(0.0)]
    assert twr_index(pts)[-1] == pytest.approx(100.0)


def test_twr_separates_flow_and_performance():
    pts = [
        {"date": D0, "equity": 100_000.0, "net_flow": 0.0},
        # +1% performance AND a $50K withdrawal the same day:
        {"date": D0 + dt.timedelta(days=1), "equity": 51_000.0, "net_flow": -50_000.0},
    ]
    assert twr_returns(pts) == [pytest.approx(0.01)]


def test_twr_funding_reset_not_minus_90pct():
    # The acct-13 case: $1M → $100K funding reset must not read as −90%.
    pts = [
        {"date": D0, "equity": 1_000_000.0, "net_flow": 0.0},
        {"date": D0 + dt.timedelta(days=1), "equity": 100_000.0, "net_flow": -900_000.0},
    ]
    assert twr_returns(pts) == [pytest.approx(0.0)]


# ---------------------------------------------------------------------------
# guardrail_sweep persists what it computes.
# ---------------------------------------------------------------------------
def test_guardrail_sweep_records_snapshots(db, user, monkeypatch):
    from apps.portfolios import autopilot_risk, tasks_autopilot

    _fund, members = _fund_of(user, n=2)
    monkeypatch.setattr(
        autopilot_risk, "evaluate_drawdown",
        lambda ap: {"equity": "101234.56", "drawdown_pct": 0.0, "state": "active"},
    )
    out = tasks_autopilot.guardrail_sweep()
    assert out["snapshots"] == 2
    for _s, acc in members:
        snap = PortfolioSnapshot.objects.get(portfolio=acc.portfolio)
        assert snap.equity == Decimal("101234.56")
        assert snap.source == PortfolioSnapshot.SOURCE_SWEEP


# ---------------------------------------------------------------------------
# GET /api/fund/history/.
# ---------------------------------------------------------------------------
def _bar(ticker, date, close):
    DailyBar.objects.create(
        ticker=ticker, date=date, open=close, high=close, low=close,
        close=Decimal(str(close)), adjusted_close=Decimal(str(close)),
        volume=1000, source="test",
    )


def test_fund_history_endpoint(db, client, user):
    fund, members = _fund_of(user, n=2)
    days = [D0 + dt.timedelta(days=i) for i in range(3)]
    for i, d in enumerate(days):
        _bar("SPY", d, 100 * (1.01 ** i))
    (s0, a0), (s1, a1) = members
    for i, d in enumerate(days):
        _snap(a0.portfolio, d, 100_000 * (1.02 ** i))            # +2%/day
        _snap(a1.portfolio, d, 100_000)                          # flat
    r = client.get("/api/fund/history/")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["benchmarks"] == ["SPY"]
    by_id = {a["strategy_id"]: a for a in body["per_account"]}
    assert by_id[s0.id]["twr_pct"] == pytest.approx((1.02 ** 2 - 1) * 100, abs=1e-3)
    assert by_id[s1.id]["twr_pct"] == pytest.approx(0.0, abs=1e-9)
    agg = body["aggregate"]
    # Aggregate: 200K → 204.04K → +2.02%.
    assert agg["twr_pct"] == pytest.approx((100_000 * 1.02 ** 2 + 100_000) / 2000 - 100, abs=1e-3)
    assert agg["points"][0]["equity"] == pytest.approx(200_000.0)
    assert agg["points"][-1]["spy"] == pytest.approx(100 * 1.01 ** 2, abs=1e-3)


def test_fund_history_aggregate_flow_adjusted(db, client, user):
    fund, members = _fund_of(user, n=1)
    (_s0, a0) = members[0]
    _snap(a0.portfolio, D0, 100_000)
    _snap(a0.portfolio, D0 + dt.timedelta(days=1), 125_000, flow="25000")  # tilt transfer
    r = client.get("/api/fund/history/")
    agg = r.json()["aggregate"]
    assert agg["twr_pct"] == pytest.approx(0.0, abs=1e-9)   # no fake +25%
    assert agg["points"][-1]["equity"] == pytest.approx(125_000.0)


def test_fund_history_member_joining_mid_grid_is_flow_not_performance(db, client, user):
    """A member whose series starts AFTER the grid begins (clean-started backfill,
    or a pod added to the fund later) must enter the aggregate as capital flow —
    not print as a fake ~+100% aggregate gain on its join date."""
    fund, members = _fund_of(user, n=2)
    (_s0, a0), (_s1, a1) = members
    d1, d2 = D0, D0 + dt.timedelta(days=1)
    _snap(a0.portfolio, d1, 100_000)
    _snap(a0.portfolio, d2, 100_000)                       # flat member
    # a1 joins on d2 with full equity but only dust recorded flow (the acct-11
    # clean-start shape: equity 100,555 / net_flow 783).
    _snap(a1.portfolio, d2, 100_555, flow="783.02")
    r = client.get("/api/fund/history/")
    agg = r.json()["aggregate"]
    assert agg["points"][-1]["equity"] == pytest.approx(200_555.0)
    assert agg["twr_pct"] == pytest.approx(0.0, abs=1e-9)  # join ≠ performance


def test_fund_history_empty(db, client, user):
    AutonomousFund.objects.create(owner=user, name="Fund")
    r = client.get("/api/fund/history/")
    body = r.json()
    assert body["available"] is False


# ---------------------------------------------------------------------------
# §C3 — backfill command (fake adapter; the real Alpaca run is a CLI step).
# ---------------------------------------------------------------------------
def test_backfill_command_with_fake_adapter(db, user, monkeypatch):
    from apps.portfolios.management.commands import backfill_portfolio_history as cmd

    _fund, members = _fund_of(user, n=1)
    _s, acc = members[0]

    class FakeAdapter:
        def __init__(self, account):
            pass

        def get_portfolio_history(self, *, period="1A", timeframe="1D"):
            return [
                (datetime(2026, 6, 1, 20, tzinfo=UTC), Decimal("100000")),
                (datetime(2026, 6, 2, 20, tzinfo=UTC), Decimal("100750.10")),
                (datetime(2026, 6, 3, 20, tzinfo=UTC), Decimal("101000")),
            ]

    monkeypatch.setattr(cmd, "get_adapter_factory", lambda code: FakeAdapter)
    out = StringIO()
    call_command(
        "backfill_portfolio_history", account=acc.id, start="2026-06-02", stdout=out,
    )
    rows = list(PortfolioSnapshot.objects.filter(portfolio=acc.portfolio))
    assert [r.date.isoformat() for r in rows] == ["2026-06-02", "2026-06-03"]
    assert all(r.source == PortfolioSnapshot.SOURCE_BACKFILL for r in rows)
    assert rows[0].equity == Decimal("100750.10")


def test_backfill_does_not_overwrite_sweep_rows(db, user, monkeypatch):
    from apps.portfolios.management.commands import backfill_portfolio_history as cmd

    _fund, members = _fund_of(user, n=1)
    _s, acc = members[0]
    record_snapshot(acc.portfolio, equity="999", on=dt.date(2026, 6, 2), net_flow="0")

    class FakeAdapter:
        def __init__(self, account):
            pass

        def get_portfolio_history(self, *, period="1A", timeframe="1D"):
            return [(datetime(2026, 6, 2, 20, tzinfo=UTC), Decimal("100750.10"))]

    monkeypatch.setattr(cmd, "get_adapter_factory", lambda code: FakeAdapter)
    call_command("backfill_portfolio_history", account=acc.id, stdout=StringIO())
    snap = PortfolioSnapshot.objects.get(portfolio=acc.portfolio, date=dt.date(2026, 6, 2))
    assert snap.equity == Decimal("999")        # kept (no --overwrite)
    call_command(
        "backfill_portfolio_history", account=acc.id, overwrite=True, stdout=StringIO(),
    )
    snap.refresh_from_db()
    assert snap.equity == Decimal("100750.10")  # overwritten on request
