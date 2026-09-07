"""Adversarial review (reviewer: brokers) — pending_open release-task proofs.

1. The per-autopilot daily caps (max_orders_per_day / max_notional_per_day_usd)
   are NOT evaluated when held orders are released, although both docstrings
   say they are; and they are not counted when the orders are held either.
   Net effect: every Friday-close cycle (the fund's only schedule) is uncapped.
2. A non-Broker exception during a release (e.g. the malformed-422 crash proven
   in test_review_brokers_idempotency.py) strands the held order in
   confirmed/submit_pending with release_after=None — never retried, only logged.
3. The wash-trade guard defers on LOCAL open status, so a stale `submitted` row
   (e.g. an account that went needs_reauth) starves every later order on that
   (account, ticker) indefinitely.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.brokers import reconcile as reconcile_mod
from apps.brokers.adapters import alpaca_paper as alpaca_mod
from apps.brokers.adapters import mock as mock_adapter
from apps.brokers.credentials import set_api_key_secret
from apps.brokers.models import BrokerAccount, BrokerOrder
from apps.portfolios import autopilot as bridge
from apps.portfolios import tasks_autopilot
from apps.portfolios.models import (
    AutopilotRun,
    Portfolio,
    PortfolioStrategy,
    StrategyAutopilot,
    Universe,
)
from tests.test_review_brokers_idempotency import StubOrder, StubTradingClient, _apierror

User = get_user_model()


@pytest.fixture(autouse=True)
def _reset_mock():
    mock_adapter.reset_state()
    yield
    mock_adapter.reset_state()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="rv-release@example.com", password="x" * 12)


@pytest.fixture
def market_open(monkeypatch):
    monkeypatch.setattr("apps.brokers.market_calendar.is_market_open", lambda *a, **k: True)
    monkeypatch.setattr(
        "apps.portfolios.tasks_autopilot.skip_when_offline", lambda *_a, **_k: False,
    )


def _account(user, *, broker="mock", label="POOL"):
    pf = Portfolio.objects.create(
        user=user, kind=Portfolio.KIND_BROKER, name=f"bk-{label}", cash_balance=Decimal("100000"),
    )
    acc = BrokerAccount.objects.create(
        user=user, broker=broker, mode=BrokerAccount.MODE_PAPER,
        account_id=f"{broker}-{label}-{user.id}", label=label, portfolio=pf,
        connection_status=BrokerAccount.STATUS_ACTIVE,
    )
    if broker == "mock":
        mock_adapter.seed_demo_book(acc, cash=Decimal("100000"))
    else:
        set_api_key_secret(acc, api_key="PK", api_secret="SK")
    return acc


def _strategy(user, name="S"):
    u = Universe.objects.create(name=f"rv-uni-{name}")
    pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_STRATEGY, name=name)
    return PortfolioStrategy.objects.create(
        user=user, name=name, universe=u, portfolio=pf,
        kind=PortfolioStrategy.KIND_LONG_SHORT, max_position_pct=Decimal("0"),
    )


def _held(account, ticker, qty, *, run=None, minutes_ago=90):
    o = BrokerOrder.objects.create(
        broker_account=account, ticker=ticker, side="buy", quantity=Decimal(qty),
        order_type="market", status=BrokerOrder.STATUS_PENDING_OPEN,
        release_after=timezone.now() - dt.timedelta(minutes=minutes_ago),
    )
    if run is not None:
        run.broker_orders.add(o)
    return o


# ---------------------------------------------------------------------------
# 1. daily caps are not evaluated at release
# ---------------------------------------------------------------------------


def test_daily_caps_are_not_enforced_when_held_orders_are_released(user, market_open, monkeypatch):
    strategy = _strategy(user)
    ap, _ = StrategyAutopilot.objects.get_or_create(strategy=strategy)
    ap.max_orders_per_day = 1
    ap.max_notional_per_day_usd = Decimal("1000")
    ap.save()
    account = _account(user)
    ap.broker_account = account
    ap.save(update_fields=["broker_account"])
    run = AutopilotRun.objects.create(
        autopilot=ap, fire_time_utc=timezone.now() - dt.timedelta(hours=1),
        status=AutopilotRun.SUBMITTED,
    )
    # Three held orders of $10k+ each (demo fill price is unavailable → they rest as
    # `submitted`; what matters is that all three reach the venue path).
    monkeypatch.setattr("apps.brokers.demo_fills.live_price", lambda *a, **k: Decimal("100"))
    for t in ("AAA", "BBB", "CCC"):
        _held(account, t, "150", run=run)

    res = tasks_autopilot.release_pending_open_orders()

    assert res == {"released": 3, "candidates": 3, "deferred": 0}
    released = BrokerOrder.objects.filter(broker_account=account).exclude(
        status=BrokerOrder.STATUS_PENDING_OPEN,
    )
    assert released.count() == 3                       # cap of 1 order / $1k ignored
    # And _prior_24h (the cap basis) had nothing to count while they were held:
    assert bridge._prior_24h(account) == (3, Decimal("45000"))  # only AFTER release


def test_prior_24h_excludes_held_orders_so_a_closed_market_cycle_never_hits_a_cap(user):
    strategy = _strategy(user)
    ap, _ = StrategyAutopilot.objects.get_or_create(strategy=strategy)
    account = _account(user)
    run = AutopilotRun.objects.create(
        autopilot=ap, fire_time_utc=timezone.now(), status=AutopilotRun.SUBMITTED,
    )
    for i in range(40):  # > default max_orders_per_day (30)
        _held(account, f"T{i:02d}", "10", run=run)
    n, notional = bridge._prior_24h(account)
    assert (n, notional) == (0, Decimal("0"))


# ---------------------------------------------------------------------------
# 2. a crash mid-release strands the held order (never re-selected)
# ---------------------------------------------------------------------------


def test_crash_during_release_strands_order_outside_every_retry_path(
    user, market_open, monkeypatch,
):
    """FIXED (B3c, partly): the release no longer *crashes* — a non-JSON 422 is
    classified as transient, so the order is parked `error/unknown` with the
    failure recorded, and the 30 s poll then asks Alpaca what really happened
    (`resolve_unknown`) instead of the row being lost in submit_pending with an
    empty error_message.

    Still outstanding (owned by the autopilot WP, see the cross-WP note): the
    release batch does not itself record a per-order outcome."""
    account = _account(user, broker="alpaca_paper")
    stub = StubTradingClient()
    stub.submit_exception = _apierror(422, "<html>bad gateway</html>")   # non-JSON 422
    monkeypatch.setattr(alpaca_mod, "_make_client", lambda api_key, api_secret: stub)
    order = _held(account, "AAPL", "5")

    res = tasks_autopilot.release_pending_open_orders()

    assert res == {"released": 0, "candidates": 1, "deferred": 0}
    order.refresh_from_db()
    # Parked as "we don't know what the venue did" — not silently stuck.
    assert order.status == BrokerOrder.STATUS_ERROR
    assert order.idempotency_state == BrokerOrder.IDEM_UNKNOWN
    assert "422" in order.error_message
    assert order.submit_attempted_at is not None

    # The poll now sees it (unknown rows are pollable) and adopts the answer.
    from apps.brokers.adapters.alpaca_paper import AlpacaPaperBroker

    stub.submit_exception = None
    resolved = StubOrder(
        id="alp-late", client_order_id=str(order.client_order_id), status="canceled",
    )
    stub.by_client_id[str(order.client_order_id)] = resolved
    stub.by_id["alp-late"] = resolved
    broker = AlpacaPaperBroker(account)
    monkeypatch.setattr(reconcile_mod, "get_broker", lambda _a: broker)
    reconcile_mod.poll_open_orders_for_account(account)

    order.refresh_from_db()
    assert order.idempotency_state == BrokerOrder.IDEM_ACKNOWLEDGED
    assert order.broker_order_id == "alp-late"


def test_one_bad_release_does_not_abort_the_rest_of_the_batch(user, market_open, monkeypatch):
    """A single order's failure must not cost the other 17 their open."""
    account = _account(user, broker="alpaca_paper")
    stub = StubTradingClient()
    monkeypatch.setattr(alpaca_mod, "_make_client", lambda api_key, api_secret: stub)
    bad = _held(account, "AAPL", "5", minutes_ago=120)
    good_a = _held(account, "TLT", "3", minutes_ago=90)
    good_b = _held(account, "XLE", "4", minutes_ago=60)

    def _submit(order_data=None):
        symbol = getattr(order_data, "symbol", "")
        if symbol == "AAPL":
            raise _apierror(422, "<html>bad gateway</html>")
        return StubOrder(
            id=f"alp-{symbol}", client_order_id=getattr(order_data, "client_order_id", ""),
            symbol=symbol, status="new",
        )

    stub.submit_order = _submit

    res = tasks_autopilot.release_pending_open_orders()

    assert res == {"released": 2, "candidates": 3, "deferred": 0}
    for order in (good_a, good_b):
        order.refresh_from_db()
        assert order.status != BrokerOrder.STATUS_PENDING_OPEN
    bad.refresh_from_db()
    assert bad.status == BrokerOrder.STATUS_ERROR
    assert bad.error_message != ""      # the failure is recorded on the row


# ---------------------------------------------------------------------------
# 3. wash-trade deferral keys on LOCAL status → a stale open row starves the ticker
# ---------------------------------------------------------------------------


def test_stale_local_open_order_starves_every_later_release_for_the_ticker(user, market_open):
    account = _account(user)
    # A `submitted` row nobody will ever poll (e.g. the account was needs_reauth
    # when the fill happened, or the order is an `error/unknown` twin — see F1).
    BrokerOrder.objects.create(
        broker_account=account, ticker="XLE", side="buy", quantity=Decimal("1"),
        order_type="market", status=BrokerOrder.STATUS_SUBMITTED, broker_order_id="ghost",
        idempotency_state=BrokerOrder.IDEM_ACKNOWLEDGED,
    )
    held = _held(account, "XLE", "10")
    for _tick in range(3):
        res = tasks_autopilot.release_pending_open_orders()
        assert res == {"released": 0, "candidates": 1, "deferred": 1}
    held.refresh_from_db()
    assert held.status == BrokerOrder.STATUS_PENDING_OPEN   # forever


# ---------------------------------------------------------------------------
# Positive proofs of the release ordering / one-per-ticker contract
# ---------------------------------------------------------------------------


def test_release_is_fifo_and_one_per_account_ticker_per_tick(user, market_open, monkeypatch):
    # rest as submitted
    monkeypatch.setattr("apps.brokers.demo_fills.live_price", lambda *a, **k: None)
    account = _account(user)
    first = _held(account, "XLE", "10", minutes_ago=120)
    second = _held(account, "XLE", "4", minutes_ago=60)
    other = _held(account, "TLT", "3", minutes_ago=30)
    res = tasks_autopilot.release_pending_open_orders()
    assert res == {"released": 2, "candidates": 3, "deferred": 1}
    first.refresh_from_db()
    second.refresh_from_db()
    other.refresh_from_db()
    assert first.status == BrokerOrder.STATUS_SUBMITTED
    assert other.status == BrokerOrder.STATUS_SUBMITTED
    assert second.status == BrokerOrder.STATUS_PENDING_OPEN   # deferred behind `first`


def test_release_does_nothing_when_market_closed(user, monkeypatch):
    monkeypatch.setattr("apps.brokers.market_calendar.is_market_open", lambda *a, **k: False)
    monkeypatch.setattr(
        "apps.portfolios.tasks_autopilot.skip_when_offline", lambda *_a, **_k: False,
    )
    account = _account(user)
    _held(account, "XLE", "10")
    assert tasks_autopilot.release_pending_open_orders() == {
        "released": 0, "reason": "market closed",
    }


def test_alpaca_422_body_shape_used_above_is_realistic():
    """Alpaca's documented duplicate response is JSON with a message.

    FIXED (B3c): a 422 whose body lacks `message` (proxy / CDN error pages) no
    longer explodes inside the recogniser — it is simply "not a duplicate"."""
    exc = _apierror(
        422, json.dumps({"code": 40010001, "message": "client_order_id must be unique"}),
    )
    assert alpaca_mod._is_duplicate_client_order_id(exc) is True
    assert alpaca_mod._is_duplicate_client_order_id(_apierror(422, "<html>")) is False
    assert alpaca_mod._is_duplicate_client_order_id(
        _apierror(422, json.dumps({"code": 42210000})),
    ) is False
    # …and the translator classifies each body without raising.
    from apps.brokers.interfaces import BrokerError, BrokerTransientError

    with pytest.raises(BrokerTransientError):
        alpaca_mod._raise_translated(_apierror(422, "<html>"), "submit_order")
    with pytest.raises(BrokerError):
        alpaca_mod._raise_translated(
            _apierror(422, json.dumps({"code": 42210000, "message": "no"})), "submit_order",
        )
