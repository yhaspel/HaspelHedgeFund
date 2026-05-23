"""End-to-end lifecycle + reconciliation tests for the brokers framework.

All tests run against `MockBroker` (the Demo broker). They exercise the
order lifecycle, confirmation gate, idempotency, reconciliation, market
calendar, broken-leg group hook, and the credential-security invariants.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.brokers.adapters import mock as mock_adapter
from apps.brokers.confirmation import (
    ConfirmationError,
    GateContext,
    gate,
)
from apps.brokers.idempotency import (
    IdempotencyConflict,
    submit_idempotent,
)
from apps.brokers.market_calendar import (
    is_market_open,
    is_trading_day,
    next_open,
)
from apps.brokers.models import (
    BrokerAccount,
    BrokerCredential,
    BrokerOrder,
    DisclaimerAcceptance,
    LiveTradingDisclaimer,
)
from apps.brokers.reconcile import (
    get_broker,
    ingest_order_fills,
    reconcile_account,
    submit_order_group,
)
from apps.portfolios.models import LedgerEntry, Portfolio

User = get_user_model()


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_mock_state():
    mock_adapter.reset_state()
    yield
    mock_adapter.reset_state()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="t@example.com", password="x" * 12)


@pytest.fixture
def other_user(db):
    return User.objects.create_user(email="o@example.com", password="x" * 12)


@pytest.fixture
def auth_client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user)
    return c


def _new_account(
    user,
    *,
    broker: str = "mock",
    mode: str = BrokerAccount.MODE_PAPER,
    label: str = "Demo book",
    cash: Decimal = Decimal("100000"),
    config: dict | None = None,
    account_id: str | None = None,
) -> BrokerAccount:
    portfolio = Portfolio.objects.create(
        user=user, name=f"Broker · {label}", kind=Portfolio.KIND_BROKER,
        cash_balance=cash,
    )
    account = BrokerAccount.objects.create(
        user=user, broker=broker, mode=mode,
        account_id=account_id or f"demo-{user.id}-{label}",
        label=label, portfolio=portfolio,
        connection_status=BrokerAccount.STATUS_ACTIVE,
        config=config or {},
    )
    mock_adapter.seed_demo_book(account, cash=cash)
    return account


def _draft(account, *, ticker="AAPL", side="buy", qty=Decimal("5"),
           order_type="market", limit_price=None) -> BrokerOrder:
    return BrokerOrder.objects.create(
        broker_account=account, ticker=ticker, side=side, quantity=qty,
        order_type=order_type, limit_price=limit_price,
    )


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------


def test_demo_account_seeds_cash(user):
    account = _new_account(user)
    broker = get_broker(account)
    snap = broker.get_account()
    assert snap.cash == Decimal("100000")
    assert account.portfolio.cash_balance == Decimal("100000")


def test_full_lifecycle_writes_broker_fill_ledger(user):
    account = _new_account(user)
    order = _draft(account, qty=Decimal("3"), limit_price=Decimal("100"))
    gate(order, GateContext(user=user))
    broker = get_broker(account)
    submit_idempotent(order=order, broker=broker)
    ingest_order_fills(order, broker)

    order.refresh_from_db()
    assert order.status == BrokerOrder.STATUS_FILLED
    assert order.broker_order_id
    assert order.idempotency_state == BrokerOrder.IDEM_ACKNOWLEDGED
    assert order.fills.count() == 1

    fill_entries = LedgerEntry.objects.filter(
        portfolio=account.portfolio, kind=LedgerEntry.KIND_BROKER_FILL,
    )
    assert fill_entries.count() == 1
    ledger = fill_entries.first()
    assert ledger.cash_delta == Decimal("-300.00")
    assert ledger.quantity_delta == Decimal("3.000000")
    account.portfolio.refresh_from_db()
    assert account.portfolio.cash_balance == Decimal("99700.00")
    # Ledger invariant: initial cash + Σ cash_delta == current cash_balance.
    total = sum(
        (e.cash_delta for e in LedgerEntry.objects.filter(portfolio=account.portfolio)),
        Decimal("0"),
    )
    assert account.portfolio.cash_balance == Decimal("100000") + total


def test_partial_fill_then_complete(user):
    account = _new_account(user, config={"partial_fill": True, "latency_ticks": 1})
    order = _draft(account, qty=Decimal("4"), limit_price=Decimal("50"))
    gate(order, GateContext(user=user))
    broker = get_broker(account)
    submit_idempotent(order=order, broker=broker)
    order.refresh_from_db()
    assert order.status == BrokerOrder.STATUS_SUBMITTED

    # First poll → partial fill arrives.
    ingest_order_fills(order, broker)
    order.refresh_from_db()
    assert order.status == BrokerOrder.STATUS_PARTIAL
    assert order.fills.count() == 1
    # Second poll → second half of fill.
    ingest_order_fills(order, broker)
    order.refresh_from_db()
    assert order.status == BrokerOrder.STATUS_FILLED
    assert order.fills.count() == 2


# ---------------------------------------------------------------------------
# confirmation gate
# ---------------------------------------------------------------------------


def test_gate_blocks_typed_confirmation_when_required(user):
    account = _new_account(user)
    # Default fallback quote is $100 → 15 * 100 = $1,500 > $1,000 threshold.
    order = _draft(account, qty=Decimal("15"))
    with pytest.raises(ConfirmationError) as exc:
        gate(order, GateContext(user=user))
    assert exc.value.code == "typed_confirmation_required"

    gate(order, GateContext(user=user, typed_confirmation="AAPL"))
    order.refresh_from_db()
    assert order.status == BrokerOrder.STATUS_CONFIRMED


def test_gate_runs_risk_check_callable(user):
    account = _new_account(user)
    order = _draft(account, qty=Decimal("3"), limit_price=Decimal("50"))
    with pytest.raises(ConfirmationError) as exc:
        gate(order, GateContext(
            user=user,
            risk_check=lambda o: ["per-position cap exceeded"],
        ))
    assert exc.value.code == "risk_rejected"


def test_gate_scheduled_job_live_mode_is_hard_blocked(user):
    account = _new_account(user, mode=BrokerAccount.MODE_LIVE)
    order = _draft(account, qty=Decimal("1"))
    with pytest.raises(ConfirmationError) as exc:
        gate(order, GateContext(
            user=user, confirmation_method=BrokerOrder.CONFIRM_SCHEDULED,
            live_confirmation="LIVE",
        ))
    assert exc.value.code == "scheduled_live_blocked"
    assert exc.value.status_code == 403


def test_gate_live_requires_phrase_and_disclaimer(user):
    account = _new_account(user, mode=BrokerAccount.MODE_LIVE)
    order = _draft(account, qty=Decimal("1"), limit_price=Decimal("50"))
    # No LIVE phrase → blocked.
    with pytest.raises(ConfirmationError) as exc:
        gate(order, GateContext(user=user))
    assert exc.value.code == "live_phrase_required"

    # With phrase but no disclaimer record → blocked.
    with pytest.raises(ConfirmationError) as exc:
        gate(order, GateContext(user=user, live_confirmation="I accept LIVE risk"))
    assert exc.value.code == "disclaimer_missing"

    d = LiveTradingDisclaimer.objects.create(
        version="v1", body="legal text",
        effective_from=timezone.now(), is_current=True,
    )
    with pytest.raises(ConfirmationError) as exc:
        gate(order, GateContext(user=user, live_confirmation="LIVE"))
    assert exc.value.code == "disclaimer_outdated"

    DisclaimerAcceptance.objects.create(user=user, disclaimer=d)
    gate(order, GateContext(user=user, live_confirmation="LIVE"))
    order.refresh_from_db()
    assert order.status == BrokerOrder.STATUS_CONFIRMED


def test_gate_writes_audit_with_request_metadata(user):
    account = _new_account(user)
    order = _draft(account, qty=Decimal("2"), limit_price=Decimal("10"))
    gate(order, GateContext(
        user=user,
        ip_address="10.0.0.1",
        user_agent="pytest-runner/1.0",
    ))
    order.refresh_from_db()
    assert order.confirmed_by_id == user.id
    assert order.confirmation_ip == "10.0.0.1"
    assert order.confirmation_user_agent == "pytest-runner/1.0"
    assert order.confirmation_audit["mode"] == "paper"
    assert order.confirmation_audit["confirmation_method"] == "manual_ui"


# ---------------------------------------------------------------------------
# idempotency
# ---------------------------------------------------------------------------


def test_duplicate_submit_is_refused(user):
    account = _new_account(user)
    order = _draft(account, qty=Decimal("1"), limit_price=Decimal("10"))
    gate(order, GateContext(user=user))
    broker = get_broker(account)
    submit_idempotent(order=order, broker=broker)
    with pytest.raises(IdempotencyConflict):
        submit_idempotent(order=order, broker=broker)


def test_unknown_response_is_adopted_on_next_poll(user):
    account = _new_account(user)
    order = _draft(account, qty=Decimal("1"), limit_price=Decimal("10"))
    gate(order, GateContext(user=user))

    # Pretend the network call failed mid-flight by manually walking the
    # order into idem=unknown after the broker has accepted it.
    broker = get_broker(account)
    submit_idempotent(order=order, broker=broker)
    saved_broker_id = order.broker_order_id
    BrokerOrder.objects.filter(pk=order.pk).update(
        idempotency_state=BrokerOrder.IDEM_UNKNOWN,
        broker_order_id="",
        status=BrokerOrder.STATUS_ERROR,
        error_message="network timeout",
    )
    order.refresh_from_db()

    # Next reconcile / poll should locate the broker order by client_id and
    # adopt it.
    from apps.brokers.idempotency import resolve_unknown
    new_status = resolve_unknown(order, broker)
    assert new_status is not None
    order.refresh_from_db()
    assert order.idempotency_state == BrokerOrder.IDEM_ACKNOWLEDGED
    assert order.broker_order_id == saved_broker_id


def test_double_acknowledge_blocked_by_unique_constraint(user):
    account = _new_account(user)
    o1 = _draft(account, qty=Decimal("1"), limit_price=Decimal("10"))
    o2 = _draft(account, ticker="MSFT", qty=Decimal("1"), limit_price=Decimal("10"))
    gate(o1, GateContext(user=user))
    gate(o2, GateContext(user=user))
    broker = get_broker(account)
    submit_idempotent(order=o1, broker=broker)
    submit_idempotent(order=o2, broker=broker)
    # Manually try to assign the same broker_order_id — DB constraint must
    # block it.
    from django.db import IntegrityError
    with pytest.raises(IntegrityError):
        BrokerOrder.objects.filter(pk=o2.pk).update(broker_order_id=o1.broker_order_id)


# ---------------------------------------------------------------------------
# reconciliation + drift
# ---------------------------------------------------------------------------


def test_reconciliation_squares_out_of_band_drift(user):
    account = _new_account(user)
    # Inject a position into the mock broker that the local portfolio
    # doesn't know about (simulates an out-of-band broker-UI trade).
    mock_adapter.configure_account(
        account, positions={"GOOG": (Decimal("4"), Decimal("150"))},
        cash=Decimal("99400"),
    )
    event = reconcile_account(account)
    assert event.drift_detected is True
    account.refresh_from_db()
    assert account.portfolio.cash_balance == Decimal("99400.00")
    assert account.portfolio.positions.filter(ticker="GOOG").count() == 1
    rows = LedgerEntry.objects.filter(
        portfolio=account.portfolio, kind=LedgerEntry.KIND_RECONCILE,
    )
    assert rows.count() >= 1


def test_post_order_reconciliation_no_drift(user):
    account = _new_account(user)
    order = _draft(account, qty=Decimal("2"), limit_price=Decimal("20"))
    gate(order, GateContext(user=user))
    broker = get_broker(account)
    submit_idempotent(order=order, broker=broker)
    ingest_order_fills(order, broker)
    event = reconcile_account(account)
    assert event.drift_detected is False


# ---------------------------------------------------------------------------
# market calendar
# ---------------------------------------------------------------------------


def test_market_calendar_holiday_is_closed():
    # New Year's Day 2026
    assert is_trading_day(datetime(2026, 1, 1).date()) is False
    # The next opening after Jan 1, 2026 is Jan 2 (Friday).
    expected = datetime(2026, 1, 2, 9, 30, tzinfo=next_open(datetime(2026, 1, 1, 12, 0)).tzinfo)
    assert next_open(datetime(2026, 1, 1, 12, 0)) == expected


def test_market_calendar_open_window():
    # Monday at 10:00 local should be open.
    monday = datetime(2026, 6, 1, 10, 0, tzinfo=next_open(datetime(2026, 1, 1)).tzinfo)
    assert is_market_open(monday) is True
    # 03:00 same day should be closed.
    pre = monday.replace(hour=3)
    assert is_market_open(pre) is False


# ---------------------------------------------------------------------------
# broken-leg group
# ---------------------------------------------------------------------------


def test_broken_leg_does_not_submit_remaining(user):
    account = _new_account(user, config={"reject_next": True})
    o1 = _draft(account, ticker="AAA", limit_price=Decimal("10"))
    o2 = _draft(account, ticker="BBB", limit_price=Decimal("10"))
    gate(o1, GateContext(user=user))
    gate(o2, GateContext(user=user))
    result = submit_order_group(orders=[o1, o2])
    assert result["status"] == "partially_submitted"
    assert result["submitted"] == 0
    o1.refresh_from_db()
    o2.refresh_from_db()
    # Leg 1 was rejected by the broker. Leg 2 was NEVER sent, so it must
    # remain in 'confirmed' state — surfaced on the Pending Orders page
    # under the same group_id and awaiting an explicit human decision.
    assert o1.status == BrokerOrder.STATUS_REJECTED
    assert o2.status == BrokerOrder.STATUS_CONFIRMED
    assert o2.idempotency_state == BrokerOrder.IDEM_UNSUBMITTED
    assert o1.group_id is not None
    assert o1.group_id == o2.group_id


# ---------------------------------------------------------------------------
# multi-account + isolation
# ---------------------------------------------------------------------------


def test_multi_account_attribution(user):
    a = _new_account(user, label="A")
    b = _new_account(user, label="B")
    o_a = _draft(a, qty=Decimal("1"), limit_price=Decimal("10"))
    o_b = _draft(b, qty=Decimal("1"), limit_price=Decimal("10"))
    gate(o_a, GateContext(user=user))
    gate(o_b, GateContext(user=user))
    submit_idempotent(order=o_a, broker=get_broker(a))
    submit_idempotent(order=o_b, broker=get_broker(b))
    assert a.orders.count() == 1
    assert b.orders.count() == 1
    assert a.orders.first().pk != b.orders.first().pk


@pytest.mark.django_db
def test_broker_portfolio_blocked_from_strategy_serializer(user):
    from apps.portfolios.models import PortfolioStrategy, Universe
    from apps.portfolios.serializers import StrategySerializer
    universe = Universe.objects.create(name="u-test", description="t", source="manual")
    account = _new_account(user)
    request = type("R", (), {"user": user})()
    serializer = StrategySerializer(
        data={"name": "x", "kind": "long_only",
              "universe": universe.id, "portfolio": account.portfolio.id},
        context={"request": request},
    )
    assert serializer.is_valid() is False
    assert "broker-backed portfolios" in str(serializer.errors)


# ---------------------------------------------------------------------------
# credential security
# ---------------------------------------------------------------------------


def test_credential_round_trip_and_serializer_masking(user):
    account = _new_account(user, broker="mock")
    # Demo broker has auth_kind=none, so we use a credential row directly.
    from apps.brokers.credentials import (
        set_api_key_secret,
        with_credential,
        zero_credential,
    )
    set_api_key_secret(account, api_key="ABC", api_secret="XYZ")
    account.refresh_from_db()
    cred = account.credential
    assert cred.encrypted_api_key  # ciphertext present
    assert cred.encrypted_api_key != "ABC"  # not plaintext

    captured = {}
    def reader(secrets):
        captured.update(secrets)
        return secrets["api_key"]
    val = with_credential(account, reader)
    assert val == "ABC"

    zero_credential(account)
    account.refresh_from_db()
    cred.refresh_from_db()
    assert not cred.has_any_secret()


def test_account_serializer_does_not_leak_credentials(auth_client, user):
    account = _new_account(user)
    from apps.brokers.credentials import set_api_key_secret
    set_api_key_secret(account, api_key="SECRET-A", api_secret="SECRET-B")

    resp = auth_client.get("/api/broker-accounts/")
    assert resp.status_code == 200
    body = resp.json()
    blob = str(body)
    assert "SECRET-A" not in blob
    assert "SECRET-B" not in blob
    accounts = body if isinstance(body, list) else body.get("results", body)
    if isinstance(accounts, list):
        assert any(a.get("credential", {}).get("status") == "set" for a in accounts)


# ---------------------------------------------------------------------------
# disclaimer model
# ---------------------------------------------------------------------------


def test_disclaimer_acceptance_records_metadata(user, auth_client):
    LiveTradingDisclaimer.objects.create(
        version="v1", body="legal text",
        effective_from=timezone.now(), is_current=True,
    )
    resp = auth_client.post(
        "/api/disclaimers/accept/", {"version": "v1"}, format="json",
        HTTP_X_FORWARDED_FOR="10.0.0.99",
        HTTP_USER_AGENT="pytest-ua/1.0",
    )
    assert resp.status_code == 200
    acc = DisclaimerAcceptance.objects.get(user=user)
    assert acc.ip_address == "10.0.0.99"
    assert acc.user_agent.startswith("pytest-ua")


def test_new_disclaimer_version_reprompts(user):
    d1 = LiveTradingDisclaimer.objects.create(
        version="v1", body="v1 body",
        effective_from=timezone.now() - timedelta(days=30), is_current=False,
    )
    d2 = LiveTradingDisclaimer.objects.create(
        version="v2", body="v2 body",
        effective_from=timezone.now(), is_current=True,
    )
    DisclaimerAcceptance.objects.create(user=user, disclaimer=d1)
    # User accepted v1 but the current is v2, so live gate should still
    # require a new acceptance.
    account = _new_account(user, mode=BrokerAccount.MODE_LIVE)
    order = _draft(account, qty=Decimal("1"), limit_price=Decimal("10"))
    with pytest.raises(ConfirmationError) as exc:
        gate(order, GateContext(user=user, live_confirmation="LIVE"))
    assert exc.value.code == "disclaimer_outdated"
    DisclaimerAcceptance.objects.create(user=user, disclaimer=d2)
    gate(order, GateContext(user=user, live_confirmation="LIVE"))
    order.refresh_from_db()
    assert order.status == BrokerOrder.STATUS_CONFIRMED
