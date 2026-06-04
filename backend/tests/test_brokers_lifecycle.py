"""End-to-end lifecycle + reconciliation tests for the brokers framework.

All tests run against `MockBroker` (the Demo broker). They exercise the
order lifecycle, confirmation gate, idempotency, reconciliation, market
calendar, broken-leg group hook, and the credential-security invariants.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

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


def test_gate_refuses_inactive_account(user):
    """P3a-2 amendment (ADR 0011 §4): a draft confirmation against a non-
    active account is blocked at the gate. Cross-cutting — protects every
    credentialed adapter from submitting into a dead session."""
    account = _new_account(user)
    order = _draft(account, qty=Decimal("1"), limit_price=Decimal("10"))
    # Flip the account to needs_reauth after the draft is created — this
    # is the exact race the amendment is defending against.
    account.connection_status = BrokerAccount.STATUS_NEEDS_REAUTH
    account.save(update_fields=["connection_status"])
    with pytest.raises(ConfirmationError) as exc:
        gate(order, GateContext(user=user))
    assert exc.value.code == "account_inactive"
    assert exc.value.status_code == 409
    # The order stays draft — the gate's side effects don't run.
    order.refresh_from_db()
    assert order.status == BrokerOrder.STATUS_DRAFT
    # Other non-active statuses are also blocked.
    for status in (
        BrokerAccount.STATUS_CONNECTING,
        BrokerAccount.STATUS_DISABLED,
        BrokerAccount.STATUS_ERROR,
    ):
        account.connection_status = status
        account.save(update_fields=["connection_status"])
        with pytest.raises(ConfirmationError) as exc:
            gate(order, GateContext(user=user))
        assert exc.value.code == "account_inactive"


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


def test_reconciliation_engine_squares_out_of_band_drift(user):
    """The drift engine brings a portfolio back in line with a broker's
    reported positions/cash.

    The demo book has no external venue, so ``reconcile_account`` is a
    no-op for it (see ``test_demo_sync_rechecks_resting_orders``). The
    engine itself is therefore exercised directly here — which is exactly
    how a real (credentialed) broker adapter will drive it.
    """
    from apps.brokers.interfaces import PositionSnapshot
    from apps.brokers.models import BrokerSyncEvent
    from apps.brokers.reconcile import (
        apply_drift_as_ledger_entries,
        compute_drift,
    )

    account = _new_account(user)
    portfolio = account.portfolio
    broker_positions = [
        PositionSnapshot(
            ticker="GOOG", quantity=Decimal("4"), avg_cost=Decimal("150"),
        ),
    ]
    drift = compute_drift(
        broker_positions=broker_positions,
        broker_cash=Decimal("99400"),
        portfolio=portfolio,
    )
    assert drift.has_differences is True

    event = BrokerSyncEvent.objects.create(
        broker_account=account,
        triggered_by=BrokerSyncEvent.TRIGGER_MANUAL,
        started_at=timezone.now(),
    )
    written = apply_drift_as_ledger_entries(
        drift=drift, portfolio=portfolio, event=event,
        broker_positions={"GOOG": broker_positions[0]},
    )
    assert written >= 1
    portfolio.refresh_from_db()
    assert portfolio.cash_balance == Decimal("99400.00")
    assert portfolio.positions.filter(ticker="GOOG").count() == 1
    rows = LedgerEntry.objects.filter(
        portfolio=portfolio, kind=LedgerEntry.KIND_RECONCILE,
    )
    assert rows.count() >= 1


def test_demo_sync_is_a_noop_reconcile(user):
    """``reconcile_account`` on a demo book never reports drift — the
    database is its own book of record."""
    account = _new_account(user)
    event = reconcile_account(account)
    assert event.drift_detected is False
    assert "demo re-check" in event.notes


def test_reconcile_skips_non_active_credentialed_account(user):
    """P3a-2 amendment (ADR 0011 §4): a credentialed account in needs_reauth
    /disabled/error is skipped — reconcile_account writes a "skipped"
    BrokerSyncEvent rather than calling a dead remote session.

    The demo broker (auth_kind="none") is exempt because it has no remote
    session to fail.
    """
    from decimal import Decimal as _D

    # Demo accounts are always reconcilable regardless of status — covered
    # by the existing test above. Here we simulate a credentialed account
    # by switching the broker code to one whose capability is non-none.
    # We construct a fake registry entry to avoid needing IBKR/Alpaca
    # adapters to land first.
    from apps.brokers import capabilities as caps_mod
    from apps.brokers.capabilities import BrokerCapabilities
    from apps.brokers.models import BrokerSyncEvent
    fake_cap = BrokerCapabilities(
        code="fake_credentialed",
        display_name="Fake (test only)",
        auth_kind="api_key",
        supports_paper=True, supports_live=False,
        supports_fractional=False, quantity_increment=_D("1"),
        supported_order_types=("market",), supported_time_in_force=("day",),
    )
    caps_mod._REGISTRY["fake_credentialed"] = caps_mod.BrokerRegistryEntry(
        capabilities=fake_cap, adapter_factory=lambda a: None,
    )
    try:
        account = _new_account(user, broker="fake_credentialed")
        account.connection_status = BrokerAccount.STATUS_NEEDS_REAUTH
        account.save(update_fields=["connection_status"])

        event = reconcile_account(account, triggered_by=BrokerSyncEvent.TRIGGER_MANUAL)
        assert event.drift_detected is False
        assert event.ledger_entries_written == 0
        assert "skipped" in event.notes
        assert "needs_reauth" in event.notes
        # No external call should have happened — last_synced_at stays None.
        account.refresh_from_db()
        assert account.last_synced_at is None
    finally:
        caps_mod._REGISTRY.pop("fake_credentialed", None)


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
    from apps.portfolios.models import Universe
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


# ---------------------------------------------------------------------------
# P3a-2 framework amendments: gateway_session bootstrap + OAuth-stub scope
# ---------------------------------------------------------------------------


@pytest.fixture
def _fake_gateway_capability():
    """Register a gateway_session capability for tests since the IBKR
    adapter is built incrementally — the bootstrap path is testable on
    its own before the adapter lands."""
    from apps.brokers import capabilities as caps_mod
    from apps.brokers.capabilities import BrokerCapabilities
    cap = BrokerCapabilities(
        code="fake_gateway",
        display_name="Fake Gateway (test only)",
        auth_kind="gateway_session",
        supports_paper=True, supports_live=False,
        supports_fractional=False, quantity_increment=Decimal("1"),
        supported_order_types=("market",), supported_time_in_force=("day",),
    )
    caps_mod._REGISTRY["fake_gateway"] = caps_mod.BrokerRegistryEntry(
        capabilities=cap, adapter_factory=lambda a: None,
    )
    try:
        yield cap
    finally:
        caps_mod._REGISTRY.pop("fake_gateway", None)


def test_create_account_gateway_session_uses_pending_uuid(
    auth_client, _fake_gateway_capability,
):
    """P3a-2 (ADR 0011 §2): a gateway_session create yields a draft
    BrokerAccount with account_id="pending-{uuid4}", connection_status
    ="connecting", and no credential row. Any caller-supplied account_id
    is ignored."""
    resp = auth_client.post(
        "/api/broker-accounts/",
        {
            "broker": "fake_gateway",
            "mode": "paper",
            "label": "IBKR paper test",
            "account_id": "ATTACKER-SUPPLIED-DU1234567",  # should be ignored
        },
        format="json",
    )
    assert resp.status_code == 201, resp.json()
    body = resp.json()
    assert body["account_id"].startswith("pending-")
    # UUID4 stringified is 36 chars → "pending-" + 36 = 44.
    assert len(body["account_id"]) == len("pending-") + 36
    assert "DU1234567" not in body["account_id"]
    assert body["connection_status"] == "connecting"
    assert body["mode"] == "paper"


def test_create_account_gateway_session_two_drafts_dont_collide(
    auth_client, _fake_gateway_capability,
):
    """The pending- namespace + UUID4 means two back-to-back drafts for the
    same user/broker can't trip the `(user, broker, account_id)` unique
    constraint."""
    for i in range(2):
        resp = auth_client.post(
            "/api/broker-accounts/",
            {"broker": "fake_gateway", "mode": "paper", "label": f"draft {i}"},
            format="json",
        )
        assert resp.status_code == 201, resp.json()


def test_oauth_start_rejects_gateway_session(
    auth_client, user, _fake_gateway_capability,
):
    """P3a-2 (ADR 0011 §1): /oauth/start/ used to accept gateway_session
    and return 501. That branch is removed — gateway_session goes through
    /gateway/* endpoints instead, so /oauth/start/ now returns 400."""
    # Create a gateway_session account first.
    resp = auth_client.post(
        "/api/broker-accounts/",
        {"broker": "fake_gateway", "mode": "paper", "label": "g"},
        format="json",
    )
    assert resp.status_code == 201
    acc_id = resp.json()["id"]
    # /oauth/start/ should now reject — gateway_session is not an OAuth kind.
    resp = auth_client.post(
        f"/api/broker-accounts/{acc_id}/oauth/start/", {}, format="json",
    )
    assert resp.status_code == 400
    assert "OAuth" in resp.json().get("detail", "")


# ---------------------------------------------------------------------------
# P3a-2 IBKR gateway_session connect endpoints
# ---------------------------------------------------------------------------


class _FakeIBKRSession:
    """Stub IBKRGatewaySession installed via monkeypatch. Each test sets
    its own canned responses on the instance — same shape as the real
    one but with no httpx involvement."""

    # Class-level defaults; tests override via monkeypatch.
    _tickle_payload: object = {"iserver": {"authStatus": "x"}}
    _tickle_raises: object = None
    _auth_status_payload: dict = {"authenticated": True, "connected": True, "competing": False}
    _auth_status_raises: object = None
    _accounts_payload: object = {"accounts": ["DU1234567"], "selectedAccount": "DU1234567"}
    _accounts_raises: object = None

    def __init__(self, *a, **kw) -> None:
        pass

    def __enter__(self) -> _FakeIBKRSession:
        return self

    def __exit__(self, *exc) -> None:
        return None

    def close(self) -> None:
        return None

    def tickle(self):
        if self._tickle_raises is not None:
            raise self._tickle_raises
        return self._tickle_payload

    def auth_status(self):
        if self._auth_status_raises is not None:
            raise self._auth_status_raises
        return self._auth_status_payload

    def get(self, path: str, params: dict | None = None):
        if path == "/iserver/accounts":
            if self._accounts_raises is not None:
                raise self._accounts_raises
            return self._accounts_payload
        raise AssertionError(f"unexpected GET {path}")


@pytest.fixture
def _patch_ibkr_session(monkeypatch):
    """Install _FakeIBKRSession in views.py and return the class so tests
    can override its class-level cans."""
    from apps.brokers import views as views_mod
    monkeypatch.setattr(views_mod, "IBKRGatewaySession", _FakeIBKRSession)
    # Reset class-level cans between tests so test order doesn't matter.
    _FakeIBKRSession._tickle_payload = {"iserver": {"authStatus": "x"}}
    _FakeIBKRSession._tickle_raises = None
    _FakeIBKRSession._auth_status_payload = {
        "authenticated": True, "connected": True, "competing": False,
    }
    _FakeIBKRSession._auth_status_raises = None
    _FakeIBKRSession._accounts_payload = {
        "accounts": ["DU1234567"], "selectedAccount": "DU1234567",
    }
    _FakeIBKRSession._accounts_raises = None
    return _FakeIBKRSession


def _create_ibkr_draft(auth_client) -> int:
    """Create a draft IBKR BrokerAccount and return its id."""
    resp = auth_client.post(
        "/api/broker-accounts/",
        {"broker": "ibkr", "mode": "paper", "label": "IBKR paper"},
        format="json",
    )
    assert resp.status_code == 201, resp.json()
    return resp.json()["id"]


def test_ibkr_runtime_config_exposes_login_url(auth_client, settings):
    """The wizard reads the gateway login URL from runtime-config rather
    than baking deployment topology into the frontend (ADR 0011 §1)."""
    settings.IBKR_GATEWAY_LOGIN_URL = "https://localhost:5000"
    resp = auth_client.get("/api/broker-accounts/ibkr/runtime-config/")
    assert resp.status_code == 200
    assert resp.json() == {"gateway_login_url": "https://localhost:5000"}


def test_gateway_probe_reachable_when_tickle_succeeds(
    auth_client, _patch_ibkr_session,
):
    acc_id = _create_ibkr_draft(auth_client)
    resp = auth_client.post(
        f"/api/broker-accounts/{acc_id}/gateway/probe/", {}, format="json",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reachable"] is True
    assert "payload" in body


def test_gateway_probe_reports_unreachable_on_transient(
    auth_client, _patch_ibkr_session,
):
    from apps.brokers.interfaces import BrokerTransientError
    _patch_ibkr_session._tickle_raises = BrokerTransientError("gateway down")
    acc_id = _create_ibkr_draft(auth_client)
    resp = auth_client.post(
        f"/api/broker-accounts/{acc_id}/gateway/probe/", {}, format="json",
    )
    # The endpoint always returns 200 so the wizard has a clean contract.
    assert resp.status_code == 200
    assert resp.json()["reachable"] is False
    assert "gateway down" in resp.json()["detail"]


def test_gateway_auth_status_reports_authenticated(
    auth_client, _patch_ibkr_session,
):
    acc_id = _create_ibkr_draft(auth_client)
    resp = auth_client.post(
        f"/api/broker-accounts/{acc_id}/gateway/auth-status/", {}, format="json",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is True
    assert body["connected"] is True
    assert body["ready"] is True


def test_gateway_auth_status_not_ready_when_only_authenticated(
    auth_client, _patch_ibkr_session,
):
    _patch_ibkr_session._auth_status_payload = {
        "authenticated": True, "connected": False, "competing": False,
    }
    acc_id = _create_ibkr_draft(auth_client)
    resp = auth_client.post(
        f"/api/broker-accounts/{acc_id}/gateway/auth-status/", {}, format="json",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is True
    assert body["connected"] is False
    assert body["ready"] is False


def test_gateway_discover_accounts_returns_paper_flag(
    auth_client, _patch_ibkr_session,
):
    _patch_ibkr_session._accounts_payload = {
        "accounts": ["DU1234567", "U7654321"],
        "selectedAccount": "DU1234567",
    }
    acc_id = _create_ibkr_draft(auth_client)
    resp = auth_client.post(
        f"/api/broker-accounts/{acc_id}/gateway/discover-accounts/", {}, format="json",
    )
    assert resp.status_code == 200
    accounts = resp.json()["accounts"]
    assert {"account_id": "DU1234567", "is_paper": True} in accounts
    assert {"account_id": "U7654321", "is_paper": False} in accounts


def test_gateway_activate_paper_du_account(
    auth_client, _patch_ibkr_session,
):
    acc_id = _create_ibkr_draft(auth_client)
    resp = auth_client.post(
        f"/api/broker-accounts/{acc_id}/gateway/activate/",
        {"account_id": "DU1234567"},
        format="json",
    )
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["account_id"] == "DU1234567"
    assert body["connection_status"] == "active"
    # BrokerCredential row exists with gateway_session auth_kind, no secret.
    from apps.brokers.models import BrokerCredential as _BC
    cred = _BC.objects.get(account_id=acc_id)
    assert cred.auth_kind == "gateway_session"
    assert not cred.has_any_secret()


def test_gateway_activate_rejects_non_du_id_in_paper_mode(
    auth_client, _patch_ibkr_session,
):
    """ADR 0011 §6: paper mode requires a DU-prefixed id."""
    _patch_ibkr_session._accounts_payload = {"accounts": ["U7654321"]}
    acc_id = _create_ibkr_draft(auth_client)
    resp = auth_client.post(
        f"/api/broker-accounts/{acc_id}/gateway/activate/",
        {"account_id": "U7654321"},
        format="json",
    )
    assert resp.status_code == 400
    assert "paper account" in resp.json().get("detail", "")
    # The placeholder account stays as it was — no half-activated state.
    from apps.brokers.models import BrokerAccount as _BA
    acc = _BA.objects.get(pk=acc_id)
    assert acc.account_id.startswith("pending-")
    assert acc.connection_status == "connecting"


def test_gateway_activate_refuses_unknown_account_id(
    auth_client, _patch_ibkr_session,
):
    """The picked id must be visible to the current gateway session — a
    typo / stale wizard state shouldn't write a phantom account_id."""
    _patch_ibkr_session._accounts_payload = {"accounts": ["DU1234567"]}
    acc_id = _create_ibkr_draft(auth_client)
    resp = auth_client.post(
        f"/api/broker-accounts/{acc_id}/gateway/activate/",
        {"account_id": "DU9999999"},
        format="json",
    )
    assert resp.status_code == 400
    assert "not in the current gateway session" in resp.json().get("detail", "")


def test_gateway_activate_rejects_double_connect(
    auth_client, user, _patch_ibkr_session,
):
    """If the user has already connected this IBKR account on a different
    row, activate fails before mutating either row."""
    # First create + activate.
    first_id = _create_ibkr_draft(auth_client)
    auth_client.post(
        f"/api/broker-accounts/{first_id}/gateway/activate/",
        {"account_id": "DU1234567"},
        format="json",
    )
    # Second draft — try to activate against the same IBKR id.
    second_id = _create_ibkr_draft(auth_client)
    resp = auth_client.post(
        f"/api/broker-accounts/{second_id}/gateway/activate/",
        {"account_id": "DU1234567"},
        format="json",
    )
    assert resp.status_code == 400
    assert "already have IBKR account" in resp.json().get("detail", "")


def test_gateway_endpoints_reject_non_ibkr_account(
    auth_client, user, _patch_ibkr_session,
):
    """The /gateway/* routes are IBKR-specific — a Demo / Alpaca account
    can't accidentally drive the gateway flow."""
    demo = _new_account(user)  # broker="mock"
    resp = auth_client.post(
        f"/api/broker-accounts/{demo.id}/gateway/probe/", {}, format="json",
    )
    assert resp.status_code == 400
    assert "not IBKR" in resp.json().get("detail", "")


# ---------------------------------------------------------------------------
# P3a-2: keep_ibkr_gateway_warm Celery task
# ---------------------------------------------------------------------------


def _make_ibkr_account(user, *, account_id: str, status: str, label: str = "ibkr") -> BrokerAccount:
    """An IBKR BrokerAccount with a known account_id and status. Used by
    the keep_ibkr_gateway_warm tests to avoid the wizard flow."""
    portfolio = Portfolio.objects.create(
        user=user, name=f"Broker · {label}", kind=Portfolio.KIND_BROKER,
        cash_balance=Decimal("0"),
    )
    return BrokerAccount.objects.create(
        user=user, broker="ibkr", mode="paper",
        account_id=account_id, label=label,
        portfolio=portfolio,
        connection_status=status,
    )


@pytest.fixture
def _patch_task_session(monkeypatch):
    """Install _FakeIBKRSession in tasks.py for keep_ibkr_gateway_warm
    tests. Same canned-responses shape as the views fixture."""
    from apps.brokers import tasks as tasks_mod
    monkeypatch.setattr(tasks_mod, "IBKRGatewaySession", _FakeIBKRSession)
    _FakeIBKRSession._tickle_payload = {"iserver": {"authStatus": "x"}}
    _FakeIBKRSession._tickle_raises = None
    _FakeIBKRSession._auth_status_payload = {
        "authenticated": True, "connected": True, "competing": False,
    }
    _FakeIBKRSession._auth_status_raises = None
    return _FakeIBKRSession


def test_keep_warm_noop_when_no_ibkr_accounts(_patch_task_session, db):
    """The task is benign on a deployment with no IBKR accounts — it
    doesn't even touch the gateway."""
    from apps.brokers.tasks import keep_ibkr_gateway_warm
    summary = keep_ibkr_gateway_warm()
    assert summary["monitored_accounts"] == 0
    assert summary["checked"] is False
    assert summary["transitions"] == 0


def test_keep_warm_keeps_active_account_active(user, _patch_task_session):
    """Happy path: gateway authenticated → ACTIVE accounts stay ACTIVE."""
    acc = _make_ibkr_account(
        user, account_id="DU1234567", status=BrokerAccount.STATUS_ACTIVE,
    )
    from apps.brokers.tasks import keep_ibkr_gateway_warm
    summary = keep_ibkr_gateway_warm()
    assert summary["monitored_accounts"] == 1
    assert summary["checked"] is True
    assert summary["authenticated"] is True
    assert summary["now_active"] == 1
    assert summary["now_needs_reauth"] == 0
    # No transition — already active.
    assert summary["transitions"] == 0
    acc.refresh_from_db()
    assert acc.connection_status == BrokerAccount.STATUS_ACTIVE


def test_keep_warm_recovers_needs_reauth_to_active(user, _patch_task_session):
    """ADR 0011 §4 recovery: the user logs back in via the gateway browser
    page → next tick flips needs_reauth accounts back to ACTIVE without
    requiring a re-run of the connect wizard."""
    acc = _make_ibkr_account(
        user, account_id="DU1234567",
        status=BrokerAccount.STATUS_NEEDS_REAUTH,
    )
    from apps.brokers.tasks import keep_ibkr_gateway_warm
    summary = keep_ibkr_gateway_warm()
    assert summary["authenticated"] is True
    assert summary["transitions"] == 1
    acc.refresh_from_db()
    assert acc.connection_status == BrokerAccount.STATUS_ACTIVE


def test_keep_warm_flips_active_to_needs_reauth_on_dead_session(
    user, _patch_task_session,
):
    """Gateway is reachable but session is dead (daily reset / idle
    timeout) → ACTIVE accounts flip to needs_reauth."""
    _patch_task_session._auth_status_payload = {
        "authenticated": False, "connected": False, "competing": False,
    }
    acc = _make_ibkr_account(
        user, account_id="DU1234567", status=BrokerAccount.STATUS_ACTIVE,
    )
    from apps.brokers.tasks import keep_ibkr_gateway_warm
    summary = keep_ibkr_gateway_warm()
    assert summary["authenticated"] is False
    assert summary["transitions"] == 1
    assert summary["now_needs_reauth"] == 1
    acc.refresh_from_db()
    assert acc.connection_status == BrokerAccount.STATUS_NEEDS_REAUTH


def test_keep_warm_flips_active_to_needs_reauth_on_unreachable_gateway(
    user, _patch_task_session,
):
    """Gateway is fully unreachable (container down, network split) →
    ACTIVE accounts flip to needs_reauth and the error is captured."""
    from apps.brokers.interfaces import BrokerTransientError
    _patch_task_session._auth_status_raises = BrokerTransientError("gateway down")
    acc = _make_ibkr_account(
        user, account_id="DU1234567", status=BrokerAccount.STATUS_ACTIVE,
    )
    from apps.brokers.tasks import keep_ibkr_gateway_warm
    summary = keep_ibkr_gateway_warm()
    assert summary["authenticated"] is False
    assert "auth_status failed" in (summary["error"] or "")
    acc.refresh_from_db()
    assert acc.connection_status == BrokerAccount.STATUS_NEEDS_REAUTH


def test_keep_warm_ignores_connecting_and_disabled_accounts(
    user, _patch_task_session,
):
    """Accounts in STATUS_CONNECTING (mid-wizard) and STATUS_DISABLED
    (explicitly disconnected) are NOT touched — only ACTIVE and
    NEEDS_REAUTH rows are monitored."""
    connecting = _make_ibkr_account(
        user, account_id="pending-abc",
        status=BrokerAccount.STATUS_CONNECTING, label="mid-wizard",
    )
    disabled = _make_ibkr_account(
        user, account_id="DU0000001",
        status=BrokerAccount.STATUS_DISABLED, label="disconnected",
    )
    # Gateway says authenticated — but that shouldn't touch these.
    from apps.brokers.tasks import keep_ibkr_gateway_warm
    summary = keep_ibkr_gateway_warm()
    assert summary["monitored_accounts"] == 0
    connecting.refresh_from_db()
    disabled.refresh_from_db()
    assert connecting.connection_status == BrokerAccount.STATUS_CONNECTING
    assert disabled.connection_status == BrokerAccount.STATUS_DISABLED


# ---------------------------------------------------------------------------
# poll_open_orders: per-cycle dedupe + auth-failure recovery
# (the Alpaca 401 log-storm fix)
# ---------------------------------------------------------------------------


def _alpaca_account(user, *, status=BrokerAccount.STATUS_ACTIVE):
    """A non-demo (AUTH_API_KEY) account so poll_open_orders routes through
    poll_open_orders_for_account rather than the demo book."""
    portfolio = Portfolio.objects.create(
        user=user, name="Broker · Alpaca", kind=Portfolio.KIND_BROKER,
        cash_balance=Decimal("100000"),
    )
    return BrokerAccount.objects.create(
        user=user, broker="alpaca_paper", mode=BrokerAccount.MODE_PAPER,
        account_id=f"PA-{user.id}", label="alpaca", portfolio=portfolio,
        connection_status=status,
    )


def _submitted_order(account, *, ticker="MRVL"):
    return BrokerOrder.objects.create(
        broker_account=account, ticker=ticker, side="sell",
        quantity=Decimal("1"), order_type="market",
        status=BrokerOrder.STATUS_SUBMITTED, broker_order_id=str(uuid4()),
    )


def test_poll_open_orders_scans_account_once_per_cycle(user, monkeypatch):
    """Regression: BrokerOrder.Meta.ordering leaked created_at into the
    SELECT DISTINCT, so an account with N open orders was scanned N times a
    cycle (N× broker calls + N× log lines). It must be scanned exactly once."""
    from apps.brokers import tasks as tasks_mod

    account = _alpaca_account(user)
    for _ in range(3):
        _submitted_order(account)

    seen: list[int] = []

    def _record(acc):
        seen.append(acc.pk)
        return 0

    monkeypatch.setattr(tasks_mod, "poll_open_orders_for_account", _record)
    summary = tasks_mod.poll_open_orders()

    assert seen == [account.pk]            # once, not three times
    assert summary["accounts_scanned"] == 1


def test_poll_open_orders_flips_to_needs_reauth_on_auth_error(user, monkeypatch):
    """A 401/403 (BrokerAuthError) is permanent: the account is flipped out of
    ACTIVE so the next cycle skips it — replacing the per-cycle traceback
    storm with one concise warning. Mirrors the IBKR needs_reauth path."""
    from apps.brokers import tasks as tasks_mod
    from apps.brokers.interfaces import BrokerAuthError

    account = _alpaca_account(user)
    _submitted_order(account)

    def _boom(_acc):
        raise BrokerAuthError("Alpaca get_order_by_id → 401: unauthorized.")

    monkeypatch.setattr(tasks_mod, "poll_open_orders_for_account", _boom)
    summary = tasks_mod.poll_open_orders()

    account.refresh_from_db()
    assert account.connection_status == BrokerAccount.STATUS_NEEDS_REAUTH
    assert summary["needs_reauth"] == 1

    # Next cycle: no longer ACTIVE → skipped entirely (the storm stops).
    seen: list[int] = []

    def _record(acc):
        seen.append(acc.pk)
        return 0

    monkeypatch.setattr(tasks_mod, "poll_open_orders_for_account", _record)
    summary2 = tasks_mod.poll_open_orders()
    assert seen == []
    assert summary2["accounts_scanned"] == 0


class _AuthFailBroker:
    """Minimal Broker stub whose get_order always raises the given exception."""

    def __init__(self, exc):
        self._exc = exc

    def get_order(self, broker_order_id):
        raise self._exc

    def get_recent_fills(self, since):
        return []


def test_poll_account_reraises_auth_error_from_group_anchor(user, monkeypatch):
    """Bug fix: the group-anchor path (_poll_group) used to bypass the flat
    path's try/except, so an error escaped to the broad except as a full
    traceback. An auth failure must now propagate (so the task flips the
    account); a non-auth BrokerError must be recorded on the order, not
    raised."""
    from apps.brokers import reconcile as reconcile_mod
    from apps.brokers.brackets import is_group_anchor
    from apps.brokers.interfaces import BrokerAuthError, BrokerError
    from apps.brokers.reconcile import poll_open_orders_for_account

    account = _alpaca_account(user)
    anchor = BrokerOrder.objects.create(
        broker_account=account, ticker="MRVL", side="buy",
        quantity=Decimal("1"), order_type="market",
        status=BrokerOrder.STATUS_SUBMITTED, broker_order_id=str(uuid4()),
        group_id=uuid4(), leg_role=BrokerOrder.LEG_ENTRY,
    )
    # Guard: this test is only meaningful if the order routes through the
    # group-anchor path (_poll_group), not the flat path.
    assert is_group_anchor(anchor)

    monkeypatch.setattr(
        reconcile_mod, "get_broker",
        lambda _acc: _AuthFailBroker(BrokerAuthError("401")),
    )
    with pytest.raises(BrokerAuthError):
        poll_open_orders_for_account(account)

    monkeypatch.setattr(
        reconcile_mod, "get_broker",
        lambda _acc: _AuthFailBroker(BrokerError("422 rejected")),
    )
    written = poll_open_orders_for_account(account)
    assert written == 0
    anchor.refresh_from_db()
    assert "422 rejected" in (anchor.error_message or "")


def test_ingest_order_fills_flips_to_needs_reauth_on_group_auth_error(user):
    """Parity with the poll path: a 401/403 from a group anchor in the
    post-confirm ingest path flags the account for re-auth instead of
    propagating raw (the view caller only logs)."""
    from apps.brokers.interfaces import BrokerAuthError
    from apps.brokers.reconcile import ingest_order_fills

    account = _alpaca_account(user)
    anchor = BrokerOrder.objects.create(
        broker_account=account, ticker="MRVL", side="buy",
        quantity=Decimal("1"), order_type="market",
        status=BrokerOrder.STATUS_SUBMITTED, broker_order_id=str(uuid4()),
        group_id=uuid4(), leg_role=BrokerOrder.LEG_ENTRY,
    )

    written = ingest_order_fills(anchor, _AuthFailBroker(BrokerAuthError("401")))
    assert written == 0
    account.refresh_from_db()
    assert account.connection_status == BrokerAccount.STATUS_NEEDS_REAUTH


class _AuthFailAccountBroker:
    """Broker stub whose account/position reads raise an auth error."""

    def __init__(self, exc):
        self._exc = exc

    def get_positions(self):
        raise self._exc

    def get_account(self):
        raise self._exc


def test_reconcile_account_flips_to_needs_reauth_on_auth_error(user, monkeypatch):
    """reconcile_account must not silently swallow a 401/403 as a generic
    error — it flips the account to needs_reauth (BrokerAuthError now
    subclasses BrokerError, so the auth handler must precede the generic
    one)."""
    from apps.brokers import reconcile as reconcile_mod
    from apps.brokers.interfaces import BrokerAuthError

    account = _alpaca_account(user)
    monkeypatch.setattr(
        reconcile_mod, "get_broker",
        lambda _acc: _AuthFailAccountBroker(BrokerAuthError("401")),
    )
    event = reconcile_mod.reconcile_account(account)

    account.refresh_from_db()
    assert account.connection_status == BrokerAccount.STATUS_NEEDS_REAUTH
    assert "needs_reauth" in (event.error_message or "")
