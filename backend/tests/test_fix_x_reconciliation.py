"""WP X — the cross-WP fixes the individual work packages could not land
themselves (each one lived in another WP's files), plus proof that the release
shadow caps are actually WIRED into the beat task and not merely importable:

* ``apps.brokers...bootstrap_autonomous_fund._upsert_fund`` resolves the owner's
  fund the way the API does (covered by test_review_fund_misc::test_F16),
* ``apps.data.providers.fmp.METRIC_MAP`` maps the share counts the valuation
  node needs to turn its enterprise estimates into per-share fair values,
* ``apps.portfolios.tasks.run_candidate_council`` seeds ``state["news"]`` so a
  strategy-cycle council gets real sentiment (ad-hoc runs already did),
* ``apps.portfolios.tasks_autopilot.release_pending_open_orders`` records the
  shadow-mode daily-cap evaluation on the run audit and never skips an order,
* ``apps.accounts.serializers.UserSerializer`` exposes ``is_staff`` on /api/me/.

Run:
  DJANGO_SETTINGS_MODULE=hedgefund.settings.test /tmp/v312/bin/pytest \
      tests/test_fix_x_reconciliation.py -q -p no:cacheprovider
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.brokers.adapters import mock as mock_adapter
from apps.brokers.models import BrokerAccount, BrokerOrder, StrategyBrokerLink
from apps.portfolios import tasks_autopilot
from apps.portfolios.models import (
    AutopilotRun,
    Portfolio,
    PortfolioStrategy,
    StrategyAutopilot,
    Universe,
)

User = get_user_model()


@pytest.fixture(autouse=True)
def _reset_mock():
    mock_adapter.reset_state()
    yield
    mock_adapter.reset_state()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="fix-x@x.test", password="pw-fake-123456789")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


# ===========================================================================
# X-B: FMP METRIC_MAP — the valuation node's share counts.
# ===========================================================================
_STATEMENT_ROW = {
    "date": "2026-03-31",
    "acceptedDate": "2026-04-20 16:05:00",
    "revenue": 1000.0,
    "operatingIncome": 200.0,
    "netIncome": 150.0,
    "freeCashFlow": 120.0,
    "totalStockholdersEquity": 900.0,
    "totalAssets": 2000.0,
    "weightedAverageShsOut": 1_000_000.0,
    "weightedAverageShsOutDil": 1_050_000.0,
}


class _StatementHttp:
    """Returns the same statement row for every endpoint FMP is asked for."""

    def __init__(self):
        self.urls: list[str] = []

    def get(self, url, params=None):
        self.urls.append(url)
        rows = [_STATEMENT_ROW]

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return rows

        return _Resp()


def test_fmp_metric_map_resolves_every_share_count_the_valuation_node_asks_for(db):
    """Without these entries FmpProvider filters both share-count metrics out
    as unknown, ``valuation._share_count`` finds nothing, and the node reports
    ``upside_pct=None`` on EVERY ticker instead of a real band."""
    from apps.data.providers.fmp import METRIC_MAP, FmpProvider
    from hedgefund_agents.analytical.valuation import METRICS, SHARE_COUNT_METRICS

    # 1. Every metric the node requests is mappable (nothing silently dropped).
    assert set(METRICS) <= set(METRIC_MAP)
    assert set(SHARE_COUNT_METRICS) <= set(METRIC_MAP)
    # 2. Diluted is the authoritative one (SHARE_COUNT_METRICS[0]).
    assert METRIC_MAP["shares_outstanding"] == (
        "income-statement", "weightedAverageShsOutDil",
    )
    assert METRIC_MAP["weighted_average_shares_outstanding"] == (
        "income-statement", "weightedAverageShsOut",
    )

    # 3. ...and they survive an actual fetch → cache → point-in-time read.
    http = _StatementHttp()
    rows = FmpProvider(api_key="test-key", http=http).get_fundamentals(
        "AAPL", list(METRICS), as_of=dt.date(2026, 6, 1),
    )
    by_metric = {r.metric: r.value for r in rows}
    assert by_metric["shares_outstanding"] == Decimal("1050000.000000")
    assert by_metric["weighted_average_shares_outstanding"] == Decimal("1000000.000000")


# ===========================================================================
# X-C: run_candidate_council seeds state["news"].
# ===========================================================================
class _StubGraph:
    def __init__(self, sink):
        self._sink = sink

    def invoke(self, state):
        self._sink.append(state)
        return {"decision": {"ticker": state["ticker"], "action": "hold", "rationale": "x"},
                "risk": {}}


def test_run_candidate_council_seeds_the_news_batch_for_sentiment(user, monkeypatch):
    """sentiment and news_digest are siblings in the analytical fan-out, so
    sentiment can never see the digest's output. apps.runs.tasks.execute_run
    seeds state["news"] for ad-hoc runs; the strategy-cycle council did not, so
    every cycle candidate scored sentiment 0.0 with no drivers."""
    from apps.portfolios.tasks import run_candidate_council
    from hedgefund_agents.analytical.sentiment import NewsBatch

    fetched: list[tuple] = []

    class _Service:
        def fetch_and_persist(self, ticker, *, as_of, lookback_days):
            fetched.append((ticker, as_of, lookback_days))
            return [
                SimpleNamespace(
                    headline="Acme beats on margins",
                    summary="Gross margin up 300bps.",
                    raw_text="", published_at=dt.datetime(2026, 5, 30, 12, 0),
                ),
            ]

    states: list[dict] = []
    monkeypatch.setattr(
        "apps.data.providers.factory.get_news_service", lambda **kw: _Service(),
    )
    for name in ("get_fmp_provider", "get_edgar_provider", "get_ownership_provider"):
        monkeypatch.setattr(f"apps.portfolios.tasks.{name}", lambda *a, **k: object())
    monkeypatch.setattr(
        "apps.portfolios.tasks.build_council_graph", lambda **kw: _StubGraph(states),
    )

    run_candidate_council(
        {"ticker": "AAPL", "sector": "Tech", "side": "long",
         "as_of_date": "2026-06-01", "user_id": user.id},
    )

    assert fetched == [("AAPL", dt.date(2026, 6, 1), 30)]
    news = states[0]["news"]
    assert isinstance(news, NewsBatch)
    assert news.headlines == ["Acme beats on margins"]
    assert not news.is_empty()


def test_run_candidate_council_news_seed_is_best_effort(user, monkeypatch):
    """A provider outage must not fail the candidate: the seed degrades to an
    empty batch and the sentiment node falls back to neutral as before."""
    from apps.portfolios.tasks import run_candidate_council
    from hedgefund_agents.analytical.sentiment import NewsBatch

    states: list[dict] = []

    def _boom(**kw):
        raise RuntimeError("news provider down")

    monkeypatch.setattr("apps.data.providers.factory.get_news_service", _boom)
    for name in ("get_fmp_provider", "get_edgar_provider", "get_ownership_provider"):
        monkeypatch.setattr(f"apps.portfolios.tasks.{name}", lambda *a, **k: object())
    monkeypatch.setattr(
        "apps.portfolios.tasks.build_council_graph", lambda **kw: _StubGraph(states),
    )

    out = run_candidate_council(
        {"ticker": "AAPL", "sector": "Tech", "side": "long",
         "as_of_date": "2026-06-01", "user_id": user.id},
    )
    assert out["decision"]["action"] == "hold"
    assert states[0]["news"] == NewsBatch.empty()


# ===========================================================================
# X-D: release_pending_open_orders records the shadow caps on the run audit.
# ===========================================================================
def _account(user, *, label="POOL"):
    pf = Portfolio.objects.create(
        user=user, kind=Portfolio.KIND_BROKER, name=f"bk-{label}",
        cash_balance=Decimal("100000"),
    )
    acc = BrokerAccount.objects.create(
        user=user, broker="mock", mode=BrokerAccount.MODE_PAPER,
        account_id=f"mock-{label}-{user.id}", label=label, portfolio=pf,
        connection_status=BrokerAccount.STATUS_ACTIVE,
    )
    mock_adapter.seed_demo_book(acc, cash=Decimal("100000"))
    return acc


def _autopilot(user, account, **kw):
    u = Universe.objects.create(name=f"fix-x-uni-{kw.pop('uni', '0')}")
    pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_STRATEGY, name="sx")
    strategy = PortfolioStrategy.objects.create(
        user=user, name="S", universe=u, portfolio=pf,
        kind=PortfolioStrategy.KIND_LONG_SHORT, max_position_pct=Decimal("0"),
    )
    # The legacy (non-sleeve) resolution path — release_caps._autopilot_for
    # finds the governing autopilot through the account's active link.
    StrategyBrokerLink.objects.create(strategy=strategy, broker_account=account)
    ap, _ = StrategyAutopilot.objects.get_or_create(strategy=strategy)
    ap.broker_account = account
    for key, value in kw.items():
        setattr(ap, key, value)
    ap.save()
    return ap


def _held(account, ticker, qty, *, run):
    order = BrokerOrder.objects.create(
        broker_account=account, ticker=ticker, side="buy", quantity=Decimal(qty),
        order_type="market", status=BrokerOrder.STATUS_PENDING_OPEN,
        release_after=timezone.now() - dt.timedelta(minutes=90),
    )
    run.broker_orders.add(order)
    return order


@pytest.fixture
def market_open(monkeypatch):
    monkeypatch.setattr("apps.brokers.market_calendar.is_market_open", lambda *a, **k: True)
    monkeypatch.setattr(
        "apps.portfolios.tasks_autopilot.skip_when_offline", lambda *_a, **_k: False,
    )


def test_release_records_the_shadow_caps_without_skipping_an_order(
    user, market_open, monkeypatch,
):
    """Owner decision: the daily caps at RELEASE are shadow mode — computed and
    recorded, never blocking. So a 1-order/day cap must still release all three
    held orders AND leave a caps_shadow record naming the two it would have
    refused."""
    monkeypatch.setattr("apps.brokers.demo_fills.live_price", lambda *a, **k: Decimal("100"))
    account = _account(user)
    ap = _autopilot(
        user, account, max_orders_per_day=1, max_notional_per_day_usd=Decimal("100000"),
    )
    run = AutopilotRun.objects.create(
        autopilot=ap, fire_time_utc=timezone.now() - dt.timedelta(hours=1),
        status=AutopilotRun.SUBMITTED,
    )
    orders = [_held(account, t, "150", run=run) for t in ("AAA", "BBB", "CCC")]

    res = tasks_autopilot.release_pending_open_orders()

    assert res == {"released": 3, "candidates": 3, "deferred": 0}   # nothing blocked
    run.refresh_from_db()
    shadow = run.submit_decision["caps_shadow"]
    assert shadow["shadow"] is True
    assert shadow["would_skip"] == [o.pk for o in orders[1:]]       # the 1/day cap
    assert "daily order cap" in shadow["reason"]
    record = run.submit_decision["release"][-1]
    assert record["released"] == [o.pk for o in orders]
    assert record["skipped"] == [] and record["failed"] == []
    assert record["caps_shadow"]["would_skip"] == [o.pk for o in orders[1:]]


def test_release_of_one_bad_order_does_not_abort_the_rest_of_the_batch(
    user, market_open, monkeypatch,
):
    """A single order that blows up at the venue is bucketed as `failed`; the
    others still release and the audit record still lands."""
    monkeypatch.setattr("apps.brokers.demo_fills.live_price", lambda *a, **k: Decimal("100"))
    account = _account(user)
    ap = _autopilot(user, account)
    run = AutopilotRun.objects.create(
        autopilot=ap, fire_time_utc=timezone.now() - dt.timedelta(hours=1),
        status=AutopilotRun.SUBMITTED,
    )
    bad = _held(account, "AAA", "150", run=run)
    good = [_held(account, t, "150", run=run) for t in ("BBB", "CCC")]

    from apps.portfolios import autopilot as bridge_mod

    def _flaky(order):
        if order.pk == bad.pk:
            raise ValueError("malformed 422 from the venue")
        return True

    with patch.object(bridge_mod, "submit_held_order", _flaky):
        res = tasks_autopilot.release_pending_open_orders()

    assert res == {"released": 2, "candidates": 3, "deferred": 0}
    run.refresh_from_db()
    record = run.submit_decision["release"][-1]
    assert record["failed"] == [bad.pk]
    assert record["released"] == [o.pk for o in good]
    assert "caps_shadow" in record


# ===========================================================================
# X-E: /api/me/ exposes is_staff.
# ===========================================================================
def test_me_endpoint_exposes_is_staff_read_only(client, user):
    """The UI hides the staff-only Settings › Models catalog buttons on this
    flag; without it they render for everyone and 403 on click."""
    r = client.get("/api/me/")
    assert r.status_code == 200, r.content
    assert r.json()["is_staff"] is False

    user.is_staff = True
    user.save(update_fields=["is_staff"])
    assert client.get("/api/me/").json()["is_staff"] is True

    # Read-only: a client cannot promote itself through the serializer.
    from apps.accounts.serializers import UserSerializer

    assert "is_staff" in UserSerializer.Meta.read_only_fields
    ser = UserSerializer(instance=user, data={"is_staff": False}, partial=True)
    assert ser.is_valid(), ser.errors
    assert "is_staff" not in ser.validated_data
