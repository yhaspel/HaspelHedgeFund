"""verify_friday_cycle reads the live pod layout from the enabled autopilots.

It used to hard-code pod strategy ids, the risk-parity book and the Alpaca
account of an older roster, so it expected a retired pod to fire and probed a
retired account. The pods, the account they trade and their universes now come
from enabled StrategyAutopilot rows, and the command stays strictly read-only.
"""
from __future__ import annotations

import datetime as dt
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.brokers.models import BrokerAccount
from apps.data.models import DailyBar
from apps.portfolios.models import (
    AutopilotRun,
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    StrategyAutopilot,
    Universe,
    UniverseMembership,
)

User = get_user_model()

CYCLE = dt.date(2026, 10, 30)  # a Friday
FIRE = dt.datetime(2026, 10, 30, 20, 30, tzinfo=dt.UTC)


@pytest.fixture
def user(db):
    return User.objects.create_user(email="verify-friday@x.test", password="pw-fake-123456789")


def _pod(user, name, kind, tickers, *, account=None, enabled=True):
    universe = Universe.objects.create(name=f"vfc-{kind}")
    for t in tickers:
        UniverseMembership.objects.create(
            universe=universe, ticker=t, effective_from=dt.date(2020, 1, 1),
        )
        DailyBar.objects.create(
            ticker=t, date=CYCLE, open=100, high=100, low=100, close=100,
            adjusted_close=100, volume=1_000, source="fmp",
        )
    strategy = PortfolioStrategy.objects.create(
        user=user, name=name, kind=kind, universe=universe,
        portfolio=Portfolio.objects.create(
            user=user, kind=Portfolio.KIND_STRATEGY, name=f"book {kind}",
        ),
    )
    autopilot = StrategyAutopilot.objects.create(
        strategy=strategy, broker_account=account, is_enabled=enabled,
    )
    return strategy, autopilot


@pytest.fixture
def layout(user):
    """Today's shape: a risk-parity and a trend pod on ONE shared account, plus
    an archived (disabled) pod that no longer fires."""
    account = BrokerAccount.objects.create(
        user=user, broker="alpaca_paper", mode=BrokerAccount.MODE_PAPER,
        account_id="PA-SHARED", label="Fund",
        portfolio=Portfolio.objects.create(user=user, kind=Portfolio.KIND_BROKER, name="fund"),
        connection_status=BrokerAccount.STATUS_ACTIVE,
    )
    rp, rp_ap = _pod(
        user, "Risk parity", PortfolioStrategy.KIND_RISK_PARITY, ["SPY", "TLT"], account=account,
    )
    trend, trend_ap = _pod(
        user, "Trend", PortfolioStrategy.KIND_TREND, ["GLD"], account=account,
    )
    archived, _ = _pod(
        user, "Archived sector pod", PortfolioStrategy.KIND_SECTOR_MOMENTUM, ["XLK"],
        enabled=False,
    )
    target = PortfolioTarget.objects.create(
        strategy=rp, as_of_date=CYCLE, status=PortfolioTarget.DONE,
    )
    AutopilotRun.objects.create(
        autopilot=rp_ap, fire_time_utc=FIRE, target=target, status=AutopilotRun.SUBMITTED,
    )
    AutopilotRun.objects.create(
        autopilot=trend_ap, fire_time_utc=FIRE + dt.timedelta(minutes=15),
        status=AutopilotRun.SUBMITTED,
    )
    return SimpleNamespace(account=account, rp=rp, trend=trend, trend_ap=trend_ap,
                           archived=archived)


def _broker_with_open_orders(*orders):
    return SimpleNamespace(_client=SimpleNamespace(get_orders=lambda _req: list(orders)))


def _verify(out: StringIO) -> None:
    call_command("verify_friday_cycle", "--date", CYCLE.isoformat(), stdout=out)


def test_reads_the_live_layout_from_enabled_autopilots_and_writes_nothing(layout):
    order = SimpleNamespace(symbol="SPY", side="buy", qty="3", order_type="market")
    out = StringIO()
    with patch("apps.brokers.reconcile.get_broker",
               return_value=_broker_with_open_orders(order)) as get_broker, \
         CaptureQueriesContext(connection) as queries:
        _verify(out)
    report = out.getvalue()
    assert "5/5 checks passed — ALL GREEN" in report
    assert f"[PASS] #{layout.rp.id} Risk parity: cycle fired" in report
    assert f"[PASS] #{layout.trend.id} Trend: cycle fired" in report
    assert "Archived sector pod" not in report          # disabled: not expected to fire
    assert "(3 ETFs)" in report                         # the enabled pods' universes only
    assert "[PASS] RP residuals (MRVL/SIRI/ZETA)" in report
    # The shared account is probed once, under its own id.
    get_broker.assert_called_once_with(layout.account)
    assert f"open Alpaca orders on acct {layout.account.id}: ['SPY buy 3 market']" in report
    assert queries.captured_queries                      # it did read the layout
    writes = [
        q["sql"] for q in queries.captured_queries
        if not q["sql"].lstrip().upper().startswith("SELECT")
    ]
    assert writes == []


def test_an_enabled_pod_that_did_not_fire_fails_the_verification(layout):
    AutopilotRun.objects.filter(autopilot=layout.trend_ap).delete()
    out = StringIO()
    with patch("apps.brokers.reconcile.get_broker", return_value=_broker_with_open_orders()), \
         pytest.raises(SystemExit) as exit_info:
        _verify(out)
    assert exit_info.value.code == 1
    assert (
        f"[FAIL] #{layout.trend.id} Trend: cycle fired — no AutopilotRun on {CYCLE}"
        in out.getvalue()
    )


def test_no_enabled_autopilot_is_not_reported_green(user):
    out = StringIO()
    with pytest.raises(SystemExit):
        _verify(out)
    assert "[FAIL] Enabled autopilot pods" in out.getvalue()
