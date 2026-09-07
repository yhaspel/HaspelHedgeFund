"""Review (portmisc): NAV-history TWR + graph validate — proof tests.

F-reconcile-flow : every `reconciliation_adjustment (cash)` row the broker
                   reconcile writes (missed/mispriced fills, fees, dividends,
                   corporate actions) is treated as an EXTERNAL flow by
                   snapshots.external_flow, so internal cash drift prints as
                   performance (with the wrong sign).
F-flow-timing    : r_t = (E_t - F_t)/E_{t-1} - 1 assumes flows arrive at END of
                   day; a pre-market deposit that earns the day's move is
                   booked as return.
F-graph-500      : /api/graphs/validate/ crashes on an unhashable node type.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.portfolios.models import LedgerEntry, Portfolio
from apps.portfolios.snapshots import external_flow, record_snapshot, twr_returns

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="misc@x.test", password="pw-fake-123456789")


def test_reconcile_cash_drift_is_booked_as_external_flow_and_prints_phantom_return(user):
    pf = Portfolio.objects.create(user=user, name="acct", kind=Portfolio.KIND_BROKER,
                                  cash_balance=Decimal("100000"))
    d0 = dt.date(2026, 6, 1)
    record_snapshot(pf, equity=Decimal("100000"), cash=pf.cash_balance, on=d0, net_flow=0)

    # Day 1: the reconcile discovers a fill the poller missed — +50 sh @ $100 and
    # the matching -$5,000 cash — exactly what apps/brokers/reconcile.py:509-520
    # writes: a KIND_RECONCILE row with cash_delta=-5000. Equity is unchanged.
    LedgerEntry.objects.create(
        portfolio=pf, kind=LedgerEntry.KIND_RECONCILE, ticker="", cash_delta=Decimal("-5000"),
        cash_balance_after=Decimal("95000"), note="reconciliation_adjustment (cash)",
    )
    today = dt.date.today()
    assert external_flow(pf, today) == Decimal("-5000")  # counted as a WITHDRAWAL
    snap = record_snapshot(pf, equity=Decimal("100000"), cash=Decimal("95000"), on=today)
    pts = [
        {"date": d0, "equity": 100000.0, "net_flow": 0.0},
        {"date": today, "equity": float(snap.equity), "net_flow": float(snap.net_flow)},
    ]
    # Nothing happened to the book, yet the TWR shows +5%.
    assert twr_returns(pts) == [pytest.approx(0.05)]


def test_twr_books_pre_market_deposit_growth_as_return():
    # $100K book, $100K deposited before the open, the whole $200K earns +1%.
    pts = [
        {"date": dt.date(2026, 6, 1), "equity": 100_000.0, "net_flow": 0.0},
        {"date": dt.date(2026, 6, 2), "equity": 202_000.0, "net_flow": 100_000.0},
    ]
    assert twr_returns(pts) == [pytest.approx(0.02)]  # true TWR for the day is +1%


def test_graph_validate_unhashable_node_type_is_500(user):
    c = APIClient(raise_request_exception=False)
    c.force_authenticate(user)
    r = c.post("/api/graphs/validate/",
               {"nodes": [{"id": "x", "type": ["fundamentals"]}], "edges": []}, format="json")
    assert r.status_code == 500
    r2 = c.post("/api/graphs/validate/",
                {"nodes": [{"id": "fundamentals", "type": "fundamentals"}],
                 "edges": [{"from": ["entry"], "to": "fundamentals"}]}, format="json")
    assert r2.status_code == 500
