"""WP B3c — fixes that had no proof test of their own.

Covers:
  * the Alpaca rate limiter sleeping OUTSIDE its lock;
  * transient-vs-definitive classification + retry-with-backoff on reads;
  * the `is_held` / `release_eta` order-serializer contract additions.

The idempotency / poll / calendar / fills / views fixes are proven by the
flipped tests in ``tests/test_review_brokers_*.py``.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.brokers.adapters import alpaca_paper as alpaca_mod
from apps.brokers.adapters.alpaca_paper import _RateLimiter
from apps.brokers.interfaces import BrokerAuthError, BrokerError, BrokerTransientError
from apps.brokers.market_calendar import next_open
from apps.brokers.models import BrokerAccount, BrokerOrder
from apps.brokers.serializers import BrokerOrderSerializer
from apps.portfolios.models import Portfolio

User = get_user_model()


def _apierror(status: int, body: str):
    from alpaca.common.exceptions import APIError

    class _R:
        status_code = status

    class _H:
        response = _R()
        request = None

    return APIError(body, http_error=_H())


# ---------------------------------------------------------------------------
# Rate limiter: the wait must not be held under the lock
# ---------------------------------------------------------------------------


def test_rate_limiter_does_not_sleep_holding_the_lock():
    """A throttled caller used to sleep with `self._lock` held, so every other
    thread queued behind it even though slots were free the instant the window
    rolled. Prove the lock is free while a waiter is sleeping."""
    limiter = _RateLimiter(max_calls=1, window_s=0.5)
    limiter.acquire()  # consume the only slot

    lock_free = threading.Event()
    waiter_done = threading.Event()

    def _wait_for_slot():
        limiter.acquire()
        waiter_done.set()

    t = threading.Thread(target=_wait_for_slot, daemon=True)
    t.start()
    # Give the waiter time to enter its sleep, then prove the lock is takeable.
    time.sleep(0.1)
    assert limiter._lock.acquire(timeout=0.2) is True
    limiter._lock.release()
    lock_free.set()

    t.join(timeout=3.0)
    assert waiter_done.is_set(), "waiter never got its slot"
    assert lock_free.is_set()


def test_rate_limiter_still_enforces_the_window():
    limiter = _RateLimiter(max_calls=2, window_s=0.4)
    started = time.monotonic()
    for _ in range(3):
        limiter.acquire()
    # The third call must have waited for the window to roll.
    assert time.monotonic() - started >= 0.4


# ---------------------------------------------------------------------------
# Transient vs definitive classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (429, json.dumps({"code": 42900000, "message": "too many requests"}),
         BrokerTransientError),
        (500, "internal error", BrokerTransientError),
        (503, json.dumps({"message": "upstream down"}), BrokerTransientError),
        (502, "<html>Bad Gateway</html>", BrokerTransientError),
        (422, "<html>Unprocessable</html>", BrokerTransientError),   # code-less 4xx
        (400, "not json at all", BrokerTransientError),              # code-less 4xx
        (422, json.dumps({"code": 42210000, "message": "buying power"}), BrokerError),
        (403, json.dumps({"code": 40310000}), BrokerError),          # order-domain 403
        (401, json.dumps({"message": "unauthorized."}), BrokerAuthError),
        (403, json.dumps({"code": 40110000, "message": "forbidden"}), BrokerAuthError),
    ],
    ids=lambda v: getattr(v, "__name__", str(v))[:28],
)
def test_translator_classifies_without_ever_raising_something_else(status, body, expected):
    with pytest.raises(expected) as info:
        alpaca_mod._raise_translated(_apierror(status, body), "get_account")
    # A 403 order rejection must not be mistaken for an auth wall.
    if expected is BrokerError:
        assert not isinstance(info.value, BrokerAuthError)


def test_translator_truncates_a_huge_raw_body():
    exc = _apierror(500, "x" * 5000)
    with pytest.raises(BrokerTransientError) as info:
        alpaca_mod._raise_translated(exc, "get_account")
    assert len(str(info.value)) < 400
    assert "…" in str(info.value)


def test_non_api_exceptions_are_transient():
    """A socket timeout / connection reset never reaches APIError."""
    for exc in (TimeoutError("read timed out"), ConnectionError("reset by peer")):
        with pytest.raises(BrokerTransientError):
            alpaca_mod._raise_translated(exc, "get_orders")


def test_read_calls_retry_transient_failures_with_backoff(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(alpaca_mod.time, "sleep", slept.append)
    attempts: list[int] = []

    def _flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise _apierror(503, "upstream down")
        return "ok"

    assert alpaca_mod._read_call("get_account", _flaky) == "ok"
    assert len(attempts) == 3
    assert slept == [0.5, 1.0]          # exponential backoff


def test_read_calls_give_up_after_the_retry_budget(monkeypatch):
    monkeypatch.setattr(alpaca_mod.time, "sleep", lambda _s: None)
    attempts: list[int] = []

    def _always_down():
        attempts.append(1)
        raise _apierror(500, "boom")

    with pytest.raises(BrokerTransientError):
        alpaca_mod._read_call("get_account", _always_down)
    assert len(attempts) == alpaca_mod.TRANSIENT_RETRIES + 1


def test_read_calls_do_not_retry_a_definitive_rejection(monkeypatch):
    monkeypatch.setattr(alpaca_mod.time, "sleep", lambda _s: None)
    attempts: list[int] = []

    def _rejected():
        attempts.append(1)
        raise _apierror(422, json.dumps({"code": 42210000, "message": "nope"}))

    with pytest.raises(BrokerError):
        alpaca_mod._read_call("get_order", _rejected)
    assert len(attempts) == 1           # no point retrying a "no"


def test_read_call_treats_404_as_an_answer_when_asked_to():
    def _missing():
        raise _apierror(404, json.dumps({"code": 40410000, "message": "not found"}))

    assert alpaca_mod._read_call("find", _missing, none_on_404=True) is None
    with pytest.raises(BrokerError):
        alpaca_mod._read_call("find", _missing)


# ---------------------------------------------------------------------------
# Order serializer contract: is_held / release_eta
# ---------------------------------------------------------------------------


@pytest.fixture
def account(db):
    user = User.objects.create_user(email="b3c@example.com", password="x" * 12)
    pf = Portfolio.objects.create(
        user=user, name="bk", kind=Portfolio.KIND_BROKER, cash_balance=Decimal("100000"),
    )
    return BrokerAccount.objects.create(
        user=user, broker="mock", mode="paper", account_id="b3c", label="b3c",
        portfolio=pf, connection_status=BrokerAccount.STATUS_ACTIVE,
    )


def _order(account, **kw) -> BrokerOrder:
    defaults = dict(
        broker_account=account, ticker="AAPL", side="buy", quantity=Decimal("1"),
        order_type="market",
    )
    defaults.update(kw)
    return BrokerOrder.objects.create(**defaults)


def test_ordinary_order_is_not_held_and_has_no_eta(account):
    data = BrokerOrderSerializer(_order(account)).data
    assert data["is_held"] is False
    assert data["release_eta"] is None


def test_locally_held_order_reports_its_release_after_as_the_eta(account):
    release_at = timezone.now() + dt.timedelta(hours=12)
    order = _order(
        account, status=BrokerOrder.STATUS_PENDING_OPEN, release_after=release_at,
    )
    data = BrokerOrderSerializer(order).data
    assert data["is_held"] is True
    assert data["release_eta"] == release_at.astimezone(dt.UTC).isoformat()
    assert data["release_eta"].endswith("+00:00")       # always UTC for the UI


def test_held_order_whose_session_has_passed_recomputes_from_the_calendar(account):
    order = _order(
        account, status=BrokerOrder.STATUS_PENDING_OPEN,
        release_after=timezone.now() - dt.timedelta(days=3),
    )
    data = BrokerOrderSerializer(order).data
    expected = next_open(timezone.now()).astimezone(dt.UTC).isoformat()
    assert data["is_held"] is True
    assert data["release_eta"] == expected


def test_broker_queued_order_is_held_too(account):
    """`queued_until_open` is the BROKER holding the order; the UI shows the
    same badge, with the next session open as the ETA."""
    order = _order(
        account, status=BrokerOrder.STATUS_SUBMITTED, queued_until_open=True,
    )
    data = BrokerOrderSerializer(order).data
    assert data["is_held"] is True
    assert data["release_eta"] == next_open(timezone.now()).astimezone(dt.UTC).isoformat()


def test_is_held_and_release_eta_are_read_only(account):
    """Contract: the UI reads them, it never writes them."""
    fields = BrokerOrderSerializer().fields
    assert fields["is_held"].read_only
    assert fields["release_eta"].read_only
    assert "release_after" in BrokerOrderSerializer.Meta.read_only_fields
