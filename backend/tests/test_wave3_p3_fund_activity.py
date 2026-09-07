"""Wave 3 / WP P3 — the fund activity feed and scheduler-health endpoints.

Covers the merge (broker orders / fills / cancellations, autopilot dispatches
and their release + shadow-cap audits, fund halts / resumes / resets /
flattens, guardrail transitions, sleeve reallocations), the entry contract,
pagination, and the scheduler-health overdue accounting that makes a dead
Celery beat visible.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.brokers.adapters import mock as mock_adapter
from apps.brokers.models import BrokerAccount, BrokerFill, BrokerOrder
from apps.portfolios import fund as fund_layer
from apps.portfolios import fund_activity, scheduler_health, sleeves
from apps.portfolios.models import (
    AutonomousFund,
    AutopilotRun,
    FundEvent,
    Portfolio,
    PortfolioStrategy,
    StrategyAutopilot,
    Universe,
    UniverseMembership,
)

User = get_user_model()

VALID_KINDS = {
    "order_pending_open", "order_submitted", "order_filled", "order_cancelled",
    "order_rejected", "fund_flatten", "autopilot_run", "orders_released",
    "guardrail_transition", "fund_reset", "sleeve_reallocation", "fund_halt",
    "fund_resume",
}


@pytest.fixture(autouse=True)
def _reset_mock():
    mock_adapter.reset_state()
    yield
    mock_adapter.reset_state()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="w3p3-feed@x.test", password="pw-fake-123456789")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _strategy(user, name="A"):
    u = Universe.objects.create(name=f"w3p3-uni-{name}")
    for t in ("AAPL", "MSFT"):
        UniverseMembership.objects.create(
            universe=u, ticker=t, sector="Tech", effective_from=dt.date(2020, 1, 1),
        )
    pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_STRATEGY, name=name)
    return PortfolioStrategy.objects.create(
        user=user, name=name, kind=PortfolioStrategy.KIND_LONG_SHORT,
        universe=u, portfolio=pf, auto_run_council=True,
    )


def _account(user, *, cash="100000"):
    pf = Portfolio.objects.create(
        user=user, kind=Portfolio.KIND_BROKER, name="bk-POOL", cash_balance=Decimal(cash),
    )
    acc = BrokerAccount.objects.create(
        user=user, broker="mock", mode=BrokerAccount.MODE_PAPER,
        account_id=f"mock-POOL-{user.id}", label="POOL", portfolio=pf,
        connection_status=BrokerAccount.STATUS_ACTIVE,
    )
    mock_adapter.seed_demo_book(acc, cash=Decimal(cash))
    return acc


@pytest.fixture
def fund(user):
    f = AutonomousFund.objects.create(owner=user, name="Autonomous Fund")
    sleeves.configure_account(f, _account(user))
    s = _strategy(user, "A")
    sleeves.set_members(f, [{"strategy_id": s.id, "allocation_pct": "100"}])
    sleeves.reset_fund(f)
    StrategyAutopilot.objects.filter(strategy=s).update(is_enabled=True)
    f.refresh_from_db()
    return f


def _strategy_of(fund):
    return fund.sleeves.first().strategy


def _order(fund, *, status=BrokerOrder.STATUS_SUBMITTED, ticker="AAPL", cid=None, **kw):
    sleeve = fund.sleeves.first()
    now = timezone.now()
    defaults = {
        "broker_account": fund.broker_account, "sleeve": sleeve, "ticker": ticker,
        "side": "buy", "quantity": Decimal("10"), "status": status,
        "submitted_at": now if status != BrokerOrder.STATUS_PENDING_OPEN else None,
    }
    defaults.update(kw)
    if cid:
        defaults["client_order_id"] = cid
    return BrokerOrder.objects.create(**defaults)


def _kinds(payload):
    return [e["kind"] for e in payload["entries"]]


# ---------------------------------------------------------------------------
# Entry contract
# ---------------------------------------------------------------------------
def test_every_entry_has_the_documented_shape(fund):
    order = _order(fund)
    BrokerFill.objects.create(
        order=order, broker_fill_id="f1", quantity=Decimal("10"), price=Decimal("200"),
        filled_at=timezone.now(),
    )
    AutopilotRun.objects.create(
        autopilot=_strategy_of(fund).autopilot, fire_time_utc=timezone.now(),
        status=AutopilotRun.SUBMITTED,
        submit_decision={"orders": 2, "submitted": 2, "pending_open": 0},
    )
    out = fund_activity.fund_activity(fund)
    assert out["entries"]
    for entry in out["entries"]:
        assert set(entry) == {"at", "kind", "severity", "title", "detail", "links"}
        assert entry["at"].endswith("Z")
        # A parseable UTC instant.
        dt.datetime.fromisoformat(entry["at"].replace("Z", "+00:00"))
        assert entry["kind"] in VALID_KINDS
        assert entry["severity"] in ("info", "warn", "error")
        assert entry["title"]
        assert set(entry["links"]) <= {"strategy_id", "run_id", "order_id"}
    ats = [e["at"] for e in out["entries"]]
    assert ats == sorted(ats, reverse=True), "feed must be newest-first"


def test_order_lifecycle_produces_submitted_filled_and_cancelled_entries(fund):
    submitted = _order(fund, ticker="AAPL")
    BrokerFill.objects.create(
        order=submitted, broker_fill_id="f1", quantity=Decimal("4"), price=Decimal("200"),
        filled_at=timezone.now(),
    )
    BrokerFill.objects.create(
        order=submitted, broker_fill_id="f2", quantity=Decimal("6"), price=Decimal("201"),
        filled_at=timezone.now(),
    )
    _order(
        fund, ticker="MSFT", status=BrokerOrder.STATUS_CANCELLED,
        cancelled_at=timezone.now(),
    )
    _order(fund, ticker="AAPL", status=BrokerOrder.STATUS_PENDING_OPEN,
           release_after=timezone.now() + dt.timedelta(hours=12))

    kinds = _kinds(fund_activity.fund_activity(fund))
    assert kinds.count("order_filled") == 2, "each partial fill is its own entry"
    assert "order_submitted" in kinds
    assert "order_cancelled" in kinds
    assert "order_pending_open" in kinds


def test_rejected_orders_are_error_severity(fund):
    _order(
        fund, status=BrokerOrder.STATUS_REJECTED, submitted_at=None,
        submit_attempted_at=timezone.now(), error_message="insufficient buying power",
    )
    entries = fund_activity.fund_activity(fund)["entries"]
    rejected = [e for e in entries if e["kind"] == "order_rejected"]
    assert rejected and rejected[0]["severity"] == "error"
    assert "insufficient buying power" in rejected[0]["detail"]


def test_order_entries_link_to_their_strategy_and_autopilot_run(fund):
    strategy = _strategy_of(fund)
    run = AutopilotRun.objects.create(
        autopilot=strategy.autopilot, fire_time_utc=timezone.now(),
        status=AutopilotRun.SUBMITTED,
    )
    order = _order(fund)
    run.broker_orders.add(order)

    entries = fund_activity.fund_activity(fund)["entries"]
    submitted = next(e for e in entries if e["kind"] == "order_submitted")
    assert submitted["links"] == {
        "order_id": order.id, "strategy_id": strategy.id, "run_id": run.id,
    }


# ---------------------------------------------------------------------------
# Autopilot dispatch audits
# ---------------------------------------------------------------------------
def test_skipped_pending_open_and_halt_records_reach_the_feed(fund):
    ap = _strategy_of(fund).autopilot
    AutopilotRun.objects.create(
        autopilot=ap, fire_time_utc=timezone.now(), status=AutopilotRun.SKIPPED,
        submit_decision={
            "skipped": "skipped_pending_open", "skipped_all": "skipped_pending_open",
            "message": "3 order(s) from a previous cycle are still held for the open",
            "orders": 0, "submitted": 0, "pending_open": 0,
        },
    )
    AutopilotRun.objects.create(
        autopilot=ap, fire_time_utc=timezone.now() + dt.timedelta(minutes=1),
        status=AutopilotRun.HALTED, submit_decision={"halted": "drawdown"},
    )
    entries = fund_activity.fund_activity(fund)["entries"]
    runs = [e for e in entries if e["kind"] == "autopilot_run"]
    assert len(runs) == 2
    assert {e["severity"] for e in runs} == {"warn", "error"}
    assert any("still held for the open" in e["detail"] for e in runs)
    assert any("Halted: drawdown" in e["detail"] for e in runs)


def test_shadow_cap_evaluation_is_reported_without_claiming_a_block(fund):
    AutopilotRun.objects.create(
        autopilot=_strategy_of(fund).autopilot, fire_time_utc=timezone.now(),
        status=AutopilotRun.SUBMITTED,
        submit_decision={
            "orders": 4, "submitted": 4, "pending_open": 0,
            "caps_shadow": {
                "would_skip": [11, 12], "reason": "daily order cap 30 exceeded",
                "shadow": True,
            },
        },
    )
    entry = next(
        e for e in fund_activity.fund_activity(fund)["entries"] if e["kind"] == "autopilot_run"
    )
    assert "would have skipped 2 order(s)" in entry["detail"]
    assert "shadow mode — nothing was blocked" in entry["detail"]


def test_release_records_become_their_own_entries(fund):
    released_at = timezone.now()
    AutopilotRun.objects.create(
        autopilot=_strategy_of(fund).autopilot, fire_time_utc=timezone.now(),
        status=AutopilotRun.SUBMITTED,
        submit_decision={
            "orders": 3, "submitted": 0, "pending_open": 3,
            "release": [{
                "at": released_at.isoformat(), "released": [1, 2], "skipped": [3],
                "failed": [],
            }],
        },
    )
    entries = fund_activity.fund_activity(fund)["entries"]
    release = next(e for e in entries if e["kind"] == "orders_released")
    assert release["severity"] == "warn"  # one deferred
    assert "2 released, 1 deferred, 0 rejected" in release["detail"]


def test_dispatch_time_guardrail_transition_is_surfaced(fund):
    AutopilotRun.objects.create(
        autopilot=_strategy_of(fund).autopilot, fire_time_utc=timezone.now(),
        status=AutopilotRun.HALTED,
        guardrail_actions={"drawdown": {
            "transition": True, "prior_state": "active", "state": "halted",
            "drawdown_pct": 8.1, "peak": "100000.00", "equity": "91900.00",
        }},
    )
    entry = next(
        e for e in fund_activity.fund_activity(fund)["entries"]
        if e["kind"] == "guardrail_transition"
    )
    assert entry["severity"] == "error"
    assert "active → halted" in entry["title"]


# ---------------------------------------------------------------------------
# Fund lifecycle
# ---------------------------------------------------------------------------
def test_halt_and_resume_are_recorded_and_appear_on_the_feed(fund):
    fund_layer.halt_fund(fund, reason="manual_kill_switch")
    fund.refresh_from_db()
    fund_layer.resume_fund(fund)

    assert FundEvent.objects.filter(fund=fund, kind=FundEvent.KIND_FUND_HALT).count() == 1
    assert FundEvent.objects.filter(fund=fund, kind=FundEvent.KIND_FUND_RESUME).count() == 1
    kinds = _kinds(fund_activity.fund_activity(fund))
    assert "fund_halt" in kinds
    assert "fund_resume" in kinds
    halt = next(
        e for e in fund_activity.fund_activity(fund)["entries"] if e["kind"] == "fund_halt"
    )
    assert halt["severity"] == "error"
    assert "manual_kill_switch" in halt["title"]


def test_member_changes_show_as_sleeve_reallocation_entries(fund):
    # The fixture's set_members funded the sleeve from the pool — a sleeve
    # ledger row the feed groups into one reallocation entry.
    entries = fund_activity.fund_activity(fund)["entries"]
    realloc = [e for e in entries if e["kind"] == "sleeve_reallocation"]
    assert realloc
    assert "joined the fund" in realloc[0]["detail"]
    assert realloc[0]["links"]["strategy_id"] == _strategy_of(fund).id


def test_fund_reset_shows_as_a_grouped_ledger_entry(fund):
    # Reset only writes a ledger row when it actually moves cash, so drift the
    # sleeve's book first (as a cycle's P&L would).
    sleeve = fund.sleeves.first()
    sleeve.portfolio.cash_balance = Decimal("40000")
    sleeve.portfolio.save(update_fields=["cash_balance"])

    sleeves.reset_fund(fund)
    entries = fund_activity.fund_activity(fund)["entries"]
    resets = [e for e in entries if e["kind"] == "fund_reset"]
    assert len(resets) == 1, "one entry per reset, not one per sleeve ledger row"
    assert "fund reset" in resets[0]["detail"]


def test_flatten_batch_collapses_into_one_entry(fund):
    stamp = "20260907153000"
    for ticker in ("AAPL", "MSFT"):
        _order(
            fund, ticker=ticker, status=BrokerOrder.STATUS_PENDING_OPEN,
            cid=f"flat-f{fund.id}-s{fund.sleeves.first().id}-{ticker}-{stamp}",
        )
    entries = fund_activity.fund_activity(fund)["entries"]
    flatten = [e for e in entries if e["kind"] == "fund_flatten"]
    assert len(flatten) == 1
    assert "2 closing order(s)" in flatten[0]["title"]
    assert "AAPL, MSFT" in flatten[0]["detail"]


def test_swept_guardrail_transition_is_recorded_for_a_fund_member(fund):
    ap = _strategy_of(fund).autopilot
    event = fund_activity.record_guardrail_transition(ap, {
        "transition": True, "prior_state": "active", "state": "soft_cut",
        "drawdown_pct": 5.4, "peak": "100000.00", "equity": "94600.00",
    })
    assert event is not None
    assert event.severity == FundEvent.SEVERITY_WARN
    assert "active → soft_cut" in event.title
    # A non-transition is not recorded — the sweep runs hourly.
    assert fund_activity.record_guardrail_transition(ap, {"transition": False}) is None


def test_guardrail_transition_for_a_non_member_strategy_is_not_recorded(user, fund):
    loner = _strategy(user, "Loner")
    ap = StrategyAutopilot.objects.create(strategy=loner)
    before = FundEvent.objects.count()
    assert fund_activity.record_guardrail_transition(ap, {
        "transition": True, "prior_state": "active", "state": "halted",
    }) is None
    assert FundEvent.objects.count() == before


# ---------------------------------------------------------------------------
# Pagination + endpoint
# ---------------------------------------------------------------------------
def test_limit_and_before_cursor_page_through_the_feed(fund):
    for i in range(8):
        _order(fund, ticker="AAPL", cid=f"o-{i}")
    page1 = fund_activity.fund_activity(fund, limit=3)
    assert len(page1["entries"]) == 3
    assert page1["has_more"] is True
    assert page1["next_before"]

    cursor = fund_activity.parse_before(page1["next_before"])
    page2 = fund_activity.fund_activity(fund, limit=3, before=cursor)
    assert len(page2["entries"]) == 3
    ids1 = {(e["at"], e["kind"], e["title"]) for e in page1["entries"]}
    ids2 = {(e["at"], e["kind"], e["title"]) for e in page2["entries"]}
    assert not (ids1 & ids2), "pages must not overlap"
    assert all(e["at"] < page1["next_before"] for e in page2["entries"])


def test_activity_endpoint_is_owner_scoped_and_honest_when_there_is_no_fund(client, user, db):
    resp = client.get("/api/fund/activity/")
    assert resp.status_code == 200
    assert resp.json() == {"available": False, "reason": "no fund", "entries": []}


def test_activity_endpoint_serves_the_feed(client, fund):
    _order(fund)
    resp = client.get("/api/fund/activity/?limit=5")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["available"] is True
    assert body["fund_id"] == fund.id
    assert body["limit"] == 5
    assert body["entries"]
    assert any("recorded forward only" in n for n in body["notes"])


def test_another_user_never_sees_this_funds_activity(fund, db):
    other = User.objects.create_user(email="w3p3-other@x.test", password="pw-fake-123456789")
    c = APIClient()
    c.force_authenticate(other)
    assert c.get("/api/fund/activity/").json()["available"] is False


# ---------------------------------------------------------------------------
# Scheduler health
# ---------------------------------------------------------------------------
def test_overdue_armed_autopilot_marks_the_scheduler_dead(fund):
    ap = _strategy_of(fund).autopilot
    ap.is_enabled = True
    ap.next_run_at = timezone.now() - dt.timedelta(hours=30)
    ap.save(update_fields=["is_enabled", "next_run_at"])

    out = scheduler_health.scheduler_health(fund)
    assert out["armed_count"] == 1
    assert out["overdue_count"] == 1
    row = out["autopilots"][0]
    assert row["overdue"] is True
    assert row["overdue_by_seconds"] >= 30 * 3600 - 60
    assert out["beat_alive"] is False
    assert any("not dispatching" in w for w in out["warnings"])


def test_a_future_next_run_is_not_overdue(fund):
    ap = _strategy_of(fund).autopilot
    ap.is_enabled = True
    ap.next_run_at = timezone.now() + dt.timedelta(days=2)
    ap.save(update_fields=["is_enabled", "next_run_at"])

    out = scheduler_health.scheduler_health(fund)
    assert out["overdue_count"] == 0
    assert out["autopilots"][0]["overdue"] is False
    assert out["autopilots"][0]["overdue_by_seconds"] == 0


def test_armed_without_a_next_run_is_warned_about(fund):
    ap = _strategy_of(fund).autopilot
    ap.is_enabled = True
    ap.next_run_at = None
    ap.save(update_fields=["is_enabled", "next_run_at"])

    out = scheduler_health.scheduler_health(fund)
    assert out["autopilots"][0]["armed_without_next_run"] is True
    assert any("no next run scheduled" in w for w in out["warnings"])


def test_last_beat_tick_uses_the_most_recent_real_dispatch(fund):
    run = AutopilotRun.objects.create(
        autopilot=_strategy_of(fund).autopilot, fire_time_utc=timezone.now(),
        status=AutopilotRun.SUBMITTED,
    )
    out = scheduler_health.scheduler_health(fund)
    beat = out["last_beat_tick"]
    assert beat["source"] == "AutopilotRun.started_at"
    assert beat["at"].startswith(run.started_at.strftime("%Y-%m-%d"))
    assert beat["per_tick"] is False
    # A weekly cron is not a heartbeat, so liveness is left unknown rather than
    # asserted from a stale timestamp.
    assert out["beat_alive"] is None


def test_no_dispatch_ever_is_reported_as_such_not_as_a_dead_beat(fund):
    out = scheduler_health.scheduler_health(fund)
    assert out["last_beat_tick"]["at"] is None
    assert "never" in out["last_beat_tick"]["detail"]
    assert out["beat_alive"] is None


def test_queue_depth_is_null_with_a_stated_reason(fund):
    out = scheduler_health.scheduler_health(fund)
    assert out["queue_depth"] is None
    assert "Redis" in out["queue_depth_reason"]


def test_scheduler_health_endpoint(client, fund):
    resp = client.get("/api/fund/scheduler-health/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["available"] is True
    assert set(body) >= {
        "now", "fund_id", "beat_alive", "last_beat_tick", "autopilots",
        "armed_count", "overdue_count", "queue_depth", "queue_depth_reason", "warnings",
    }


def test_scheduler_health_without_a_fund(client, user, db):
    resp = client.get("/api/fund/scheduler-health/")
    assert resp.status_code == 200
    assert resp.json() == {"available": False, "reason": "no fund"}
