"""Adversarial review (reviewer: brokers) — HTTP layer proofs.

Positive proofs (ownership scoping holds) + defects:
  * GET /api/broker/orders/?account=<non-int> → unhandled ValueError (500)
  * two different users can activate the SAME IBKR DU account through the
    shared gateway session (collision check is per-user only)
  * `confirmation_method` is free text from the client (audit pollution)
  * a pending_open order cannot be cancelled by its owner
  * the demo book has no cash / buying-power check (negative cash)
  * account delete with a live `submitted` order orphans it at the venue
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient

from apps.brokers.adapters import mock as mock_adapter
from apps.brokers.models import BrokerAccount, BrokerCredential, BrokerOrder
from apps.portfolios.models import Portfolio
from tests.test_brokers_lifecycle import _FakeIBKRSession

User = get_user_model()


@pytest.fixture(autouse=True)
def _reset_mock():
    mock_adapter.reset_state()
    yield
    mock_adapter.reset_state()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="rv-a@example.com", password="x" * 12)


@pytest.fixture
def other(db):
    return User.objects.create_user(email="rv-b@example.com", password="x" * 12)


def _client(u) -> APIClient:
    c = APIClient()
    c.force_authenticate(u)
    return c


def _demo(u, label="Demo") -> BrokerAccount:
    pf = Portfolio.objects.create(
        user=u, name=f"Broker · {label}", kind=Portfolio.KIND_BROKER,
        cash_balance=Decimal("100000"),
    )
    acc = BrokerAccount.objects.create(
        user=u, broker="mock", mode="paper", account_id=f"demo-{u.id}-{label}",
        label=label, portfolio=pf, connection_status=BrokerAccount.STATUS_ACTIVE,
    )
    mock_adapter.seed_demo_book(acc, cash=Decimal("100000"))
    return acc


# ---------------------------------------------------------------------------
# Ownership: every account / order route is scoped to request.user (positive)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/broker-accounts/{a}/overview/"),
        ("post", "/api/broker-accounts/{a}/credentials/"),
        ("post", "/api/broker-accounts/{a}/oauth/start/"),
        ("post", "/api/broker-accounts/{a}/tradestation/discover-accounts/"),
        ("post", "/api/broker-accounts/{a}/tradestation/activate/"),
        ("post", "/api/broker-accounts/{a}/gateway/probe/"),
        ("post", "/api/broker-accounts/{a}/gateway/auth-status/"),
        ("post", "/api/broker-accounts/{a}/gateway/discover-accounts/"),
        ("post", "/api/broker-accounts/{a}/gateway/activate/"),
        ("patch", "/api/broker-accounts/{a}/settings/"),
        ("delete", "/api/broker-accounts/{a}/"),
        ("post", "/api/broker-accounts/{a}/disconnect/"),
        ("post", "/api/broker-accounts/{a}/sync/"),
        ("post", "/api/broker-accounts/{a}/acknowledge-drift/"),
    ],
)
def test_account_routes_are_owner_scoped(user, other, method, path):
    victim = _demo(user)
    resp = getattr(_client(other), method)(path.format(a=victim.pk), {}, format="json")
    assert resp.status_code == 400 and resp.json().get("detail") == "not found", resp.content
    victim.refresh_from_db()
    assert victim.is_active and victim.connection_status == BrokerAccount.STATUS_ACTIVE


def test_order_routes_are_owner_scoped(user, other):
    victim = _demo(user)
    order = BrokerOrder.objects.create(
        broker_account=victim, ticker="AAPL", side="buy", quantity=Decimal("1"),
    )
    c = _client(other)
    assert c.post(f"/api/broker/orders/{order.pk}/confirm/", {}, format="json").status_code == 400
    assert c.post(f"/api/broker/orders/{order.pk}/cancel/", {}, format="json").status_code == 400
    assert c.get(f"/api/broker/orders/?account={victim.pk}").json() in ([], {"results": []}) \
        or c.get(f"/api/broker/orders/?account={victim.pk}").json().get("count") == 0
    resp = c.post(
        "/api/broker/orders/",
        {"broker_account": victim.pk, "ticker": "AAPL", "side": "buy", "quantity": "1"},
        format="json",
    )
    assert resp.status_code == 400
    order.refresh_from_db()
    assert order.status == BrokerOrder.STATUS_DRAFT


# ---------------------------------------------------------------------------
# DEFECT: non-integer ?account= → ValueError → 500
# ---------------------------------------------------------------------------


def test_order_list_with_non_int_account_filter_500s(user):
    """FIXED (B3c): a non-numeric ?account= is client input, not a server
    fault — 400 with a field error instead of an unhandled ValueError."""
    c = _client(user)
    c.raise_request_exception = False
    resp = c.get("/api/broker/orders/?account=abc")
    assert resp.status_code == 400
    assert "integer" in str(resp.json()["account"])
    # A well-formed filter still works.
    assert _client(user).get(f"/api/broker/orders/?account={_demo(user).pk}").status_code == 200


# ---------------------------------------------------------------------------
# DEFECT: the IBKR gateway session is deployment-wide; two users can bind the
# same DU account (collision check is per-user, unique constraint is per-user)
# ---------------------------------------------------------------------------


# Wave 3: IBKR is deferred behind ENABLED_BROKERS; this multi-tenant
# invariant is about the adapter, so switch it on for this test.
@override_settings(ENABLED_BROKERS=["alpaca_paper", "mock", "ibkr"])
def test_two_users_can_activate_the_same_ibkr_paper_account(user, other, monkeypatch):
    from apps.brokers import views as views_mod

    monkeypatch.setattr(views_mod, "IBKRGatewaySession", _FakeIBKRSession)
    _FakeIBKRSession._accounts_payload = {"accounts": ["DU1234567"], "selectedAccount": "DU1234567"}
    _FakeIBKRSession._accounts_raises = None
    ids = []
    for u in (user, other):
        c = _client(u)
        created = c.post(
            "/api/broker-accounts/", {"broker": "ibkr", "mode": "paper", "label": "ib"},
            format="json",
        )
        assert created.status_code == 201
        act = c.post(
            f"/api/broker-accounts/{created.json()['id']}/gateway/activate/",
            {"account_id": "DU1234567"}, format="json",
        )
        assert act.status_code == 200, act.content
        ids.append(created.json()["id"])
    bound = BrokerAccount.objects.filter(broker="ibkr", account_id="DU1234567",
                                        connection_status=BrokerAccount.STATUS_ACTIVE)
    assert bound.count() == 2
    assert {a.user_id for a in bound} == {user.id, other.id}
    assert BrokerCredential.objects.filter(account_id__in=ids).count() == 2


# ---------------------------------------------------------------------------
# DEFECT: confirmation_method is whatever the client says it is
# ---------------------------------------------------------------------------


def test_client_can_forge_confirmation_method_in_the_audit_trail(user):
    acc = _demo(user)
    pf = acc.portfolio
    # Demo orders skip the gate, so use a credential-less "alpaca_paper" draft and
    # stub the submit to isolate the gate's audit write.
    from apps.brokers import views as views_mod

    class _NoopBroker:
        def submit_order(self, ticket):
            from apps.brokers.interfaces import OrderSnapshot
            return OrderSnapshot(
                broker_order_id="x", client_order_id=ticket.client_order_id, ticker="AAPL",
                side="buy", quantity=Decimal("1"), order_type="market", limit_price=None,
                time_in_force="day", status="submitted",
            )

    paper = BrokerAccount.objects.create(
        user=user, broker="alpaca_paper", mode="paper", account_id="PA", label="alp",
        portfolio=Portfolio.objects.create(user=user, name="x", kind=Portfolio.KIND_BROKER),
        connection_status=BrokerAccount.STATUS_ACTIVE,
    )
    order = BrokerOrder.objects.create(
        broker_account=paper, ticker="AAPL", side="buy", quantity=Decimal("1"),
    )
    import unittest.mock as um

    with um.patch.object(views_mod, "get_broker", lambda a: _NoopBroker()), \
            um.patch.object(views_mod, "run_post_confirm_pipeline", lambda o, b: None):
        resp = _client(user).post(
            f"/api/broker/orders/{order.pk}/confirm/",
            {"confirmation_method": "scheduled_job"}, format="json",
        )
    assert resp.status_code == 200, resp.content
    order.refresh_from_db()
    # FIXED (B3c): confirmation_method is server-set. This endpoint IS the
    # human-in-the-loop confirmation, so the client's claim is ignored.
    assert order.confirmation_method == BrokerOrder.CONFIRM_MANUAL
    assert order.confirmation_audit["confirmation_method"] == BrokerOrder.CONFIRM_MANUAL
    assert pf is not None


# ---------------------------------------------------------------------------
# DEFECT: a pending_open order is not cancellable by its owner
# ---------------------------------------------------------------------------


def test_owner_cannot_cancel_a_pending_open_order(user):
    """FIXED (B3c): a locally-held order can be called off before release."""
    import datetime as dt

    from django.utils import timezone

    acc = _demo(user)
    held = BrokerOrder.objects.create(
        broker_account=acc, ticker="XLE", side="buy", quantity=Decimal("10"),
        status=BrokerOrder.STATUS_PENDING_OPEN,
        release_after=timezone.now() + dt.timedelta(hours=12),
    )
    resp = _client(user).post(f"/api/broker/orders/{held.pk}/cancel/", {}, format="json")
    assert resp.status_code == 200, resp.content
    assert resp.json()["status"] == BrokerOrder.STATUS_CANCELLED
    held.refresh_from_db()
    assert held.status == BrokerOrder.STATUS_CANCELLED
    assert held.cancelled_at is not None
    assert held.release_after is None            # never re-selected by the release task
    assert held.confirmation_audit["cancel_note"] == "cancelled before release"


def test_cancelling_a_pending_open_order_is_owner_scoped(user, other):
    acc = _demo(user)
    held = BrokerOrder.objects.create(
        broker_account=acc, ticker="XLE", side="buy", quantity=Decimal("10"),
        status=BrokerOrder.STATUS_PENDING_OPEN,
    )
    assert _client(other).post(
        f"/api/broker/orders/{held.pk}/cancel/", {}, format="json",
    ).status_code == 400
    held.refresh_from_db()
    assert held.status == BrokerOrder.STATUS_PENDING_OPEN


def test_terminal_orders_are_still_not_cancellable(user):
    """Control: the 409 arm still guards genuinely terminal states."""
    acc = _demo(user)
    done = BrokerOrder.objects.create(
        broker_account=acc, ticker="XLE", side="buy", quantity=Decimal("1"),
        status=BrokerOrder.STATUS_FILLED,
    )
    resp = _client(user).post(f"/api/broker/orders/{done.pk}/cancel/", {}, format="json")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "order is not cancellable"


# ---------------------------------------------------------------------------
# DEFECT: demo book has no buying-power check — cash goes negative
# ---------------------------------------------------------------------------


def test_demo_book_accepts_orders_far_beyond_cash(user, monkeypatch):
    """FIXED (B3c): the demo book is a cash account — an unfundable buy is
    rejected instead of driving cash to -$19.9M."""
    monkeypatch.setattr("apps.brokers.demo_fills.live_price", lambda *a, **k: Decimal("200"))
    acc = _demo(user)
    resp = _client(user).post(
        "/api/broker/orders/",
        {"broker_account": acc.pk, "ticker": "AAPL", "side": "buy", "quantity": "100000"},
        format="json",
    )
    assert resp.status_code == 201, resp.content
    assert resp.json()["status"] == BrokerOrder.STATUS_REJECTED
    assert "insufficient buying power" in resp.json()["error_message"]
    acc.portfolio.refresh_from_db()
    assert acc.portfolio.cash_balance == Decimal("100000")      # untouched


def test_demo_book_fills_a_buy_it_can_afford(user, monkeypatch):
    """Control: the cash guard doesn't block ordinary demo trading."""
    monkeypatch.setattr("apps.brokers.demo_fills.live_price", lambda *a, **k: Decimal("200"))
    acc = _demo(user)
    resp = _client(user).post(
        "/api/broker/orders/",
        {"broker_account": acc.pk, "ticker": "AAPL", "side": "buy", "quantity": "100"},
        format="json",
    )
    assert resp.status_code == 201, resp.content
    assert resp.json()["status"] == BrokerOrder.STATUS_FILLED
    acc.portfolio.refresh_from_db()
    assert acc.portfolio.cash_balance == Decimal("80000.00")


def test_demo_book_still_allows_a_short_sale(user, monkeypatch):
    """Sells are never cash-blocked — a short is a legitimate demo position."""
    monkeypatch.setattr("apps.brokers.demo_fills.live_price", lambda *a, **k: Decimal("200"))
    acc = _demo(user)
    resp = _client(user).post(
        "/api/broker/orders/",
        {"broker_account": acc.pk, "ticker": "AAPL", "side": "sell", "quantity": "5000"},
        format="json",
    )
    assert resp.status_code == 201, resp.content
    assert resp.json()["status"] == BrokerOrder.STATUS_FILLED


# ---------------------------------------------------------------------------
# DEFECT: deleting an account with a live `submitted` order drops the local row
# while the order stays working at the venue
# ---------------------------------------------------------------------------


def test_account_delete_allowed_with_open_submitted_order(user):
    """FIXED (B3c): deleting would cascade the local row away while the order
    stayed working at Alpaca — 409 with the offending orders instead."""
    paper = BrokerAccount.objects.create(
        user=user, broker="alpaca_paper", mode="paper", account_id="PA", label="alp",
        portfolio=Portfolio.objects.create(user=user, name="x", kind=Portfolio.KIND_BROKER),
        connection_status=BrokerAccount.STATUS_ACTIVE,
    )
    live = BrokerOrder.objects.create(
        broker_account=paper, ticker="AAPL", side="buy", quantity=Decimal("1"),
        order_type="limit", limit_price=Decimal("1"), time_in_force="gtc",
        status=BrokerOrder.STATUS_SUBMITTED, broker_order_id="alp-live-1",
        idempotency_state=BrokerOrder.IDEM_ACKNOWLEDGED,
    )
    resp = _client(user).delete(f"/api/broker-accounts/{paper.pk}/")
    assert resp.status_code == 409, resp.content
    body = resp.json()
    assert body["code"] == "open_orders"
    assert "Cancel them first" in body["detail"]
    assert body["open_orders"] == [
        {"id": live.pk, "ticker": "AAPL", "status": BrokerOrder.STATUS_SUBMITTED},
    ]
    assert BrokerOrder.objects.filter(broker_order_id="alp-live-1").exists()
    assert BrokerAccount.objects.filter(pk=paper.pk).exists()

    # Once the order is terminal the account deletes as before.
    live.status = BrokerOrder.STATUS_CANCELLED
    live.save(update_fields=["status"])
    assert _client(user).delete(f"/api/broker-accounts/{paper.pk}/").status_code == 204


def test_account_delete_refuses_while_orders_are_held_for_the_open(user):
    """A `pending_open` order is a live intent for the next session."""
    acc = _demo(user)
    BrokerOrder.objects.create(
        broker_account=acc, ticker="XLE", side="buy", quantity=Decimal("10"),
        status=BrokerOrder.STATUS_PENDING_OPEN,
    )
    resp = _client(user).delete(f"/api/broker-accounts/{acc.pk}/")
    assert resp.status_code == 409
    assert resp.json()["code"] == "open_orders"


# ---------------------------------------------------------------------------
# DEFECT: Decimal special values in order payloads are not validated
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"quantity": "NaN"},
        {"quantity": "sNaN"},
        {"quantity": "1", "order_type": "limit", "limit_price": "NaN"},
        {"quantity": "1", "order_type": "stop", "stop_price": "NaN"},
    ],
    ids=lambda p: "&".join(f"{k}={v}" for k, v in p.items()),
)
def test_nan_in_order_payload_500s_instead_of_400(user, payload):
    """FIXED (B3c): Decimal special values are rejected by validation."""
    acc = _demo(user)
    c = _client(user)
    c.raise_request_exception = False
    resp = c.post(
        "/api/broker/orders/",
        {"broker_account": acc.pk, "ticker": "AAPL", "side": "buy", **payload},
        format="json",
    )
    assert resp.status_code == 400, resp.content
    assert "finite" in str(resp.json())
    assert not BrokerOrder.objects.filter(broker_account=acc).exists()


def test_infinite_quantity_is_not_rejected_by_validation(user, monkeypatch):
    """FIXED (B3c): ±Infinity is a 400 too, and no row is written."""
    monkeypatch.setattr("apps.brokers.demo_fills.live_price", lambda *a, **k: Decimal("200"))
    acc = _demo(user)
    c = _client(user)
    c.raise_request_exception = False
    for value in ("Infinity", "-Infinity"):
        resp = c.post(
            "/api/broker/orders/",
            {"broker_account": acc.pk, "ticker": "AAPL", "side": "buy", "quantity": value},
            format="json",
        )
        assert resp.status_code == 400, resp.content
    assert not BrokerOrder.objects.filter(broker_account=acc).exists()
