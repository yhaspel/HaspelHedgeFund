"""P7 Stage C — validation gate, fund layer (aggregate/correlation/halt),
the autopilot + fund API endpoints, and autopilot notifications."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.backtests.models import Backtest, BacktestMetrics
from apps.brokers.models import BrokerAccount, StrategyBrokerLink
from apps.notifications.models import NotificationChannel, NotificationEvent
from apps.portfolios import fund as fund_layer
from apps.portfolios import sleeves
from apps.portfolios.models import (
    AutonomousFund,
    Portfolio,
    PortfolioStrategy,
    StrategyAutopilot,
    Universe,
    UniverseMembership,
)
from apps.portfolios.validation import validation_status

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="p7f@x.test", password="pw-fake-123456789")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _strategy(user, name="S", **kw):
    u = Universe.objects.create(name=f"f-uni-{name}")
    UniverseMembership.objects.create(universe=u, ticker="AAPL", effective_from=dt.date(2020, 1, 1))
    pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_STRATEGY, name=name)
    return PortfolioStrategy.objects.create(
        user=user, name=name, universe=u, portfolio=pf,
        kind=PortfolioStrategy.KIND_LONG_SHORT, **kw,
    )


def _account(user, label="A", cash="100000"):
    pf = Portfolio.objects.create(
        user=user, kind=Portfolio.KIND_BROKER, name=f"bk-{label}", cash_balance=Decimal(cash),
    )
    return BrokerAccount.objects.create(
        user=user, broker="mock", mode=BrokerAccount.MODE_PAPER,
        account_id=f"mock-{label}", label=label, portfolio=pf,
        connection_status=BrokerAccount.STATUS_ACTIVE,
    )


def _passing_backtest(strategy, *, oos="0.8", dd="4.0", stitched="0.7"):
    # P10 §B3: the gate also requires a positive STITCHED OOS Sharpe (the
    # ``sharpe`` field) and total-return-era data (the model default).
    bt = Backtest.objects.create(
        user=strategy.user, strategy=strategy, name="bt",
        start_date=dt.date(2024, 1, 1), end_date=dt.date(2025, 1, 1),
        status=Backtest.DONE,
    )
    BacktestMetrics.objects.create(
        backtest=bt, mean_oos_sharpe=Decimal(oos), max_drawdown_pct=Decimal(dd),
        sharpe=Decimal(stitched),
    )
    return bt


# --------------------------------------------------------------------------
# §9 validation gate.
# --------------------------------------------------------------------------
def test_validation_fails_without_backtest(user):
    s = _strategy(user)
    StrategyAutopilot.objects.create(strategy=s)
    out = validation_status(s)
    assert out["passed"] is False
    assert any(c["key"] == "has_backtest" and not c["ok"] for c in out["checks"])


def test_validation_passes_with_good_backtest(user):
    s = _strategy(user)
    StrategyAutopilot.objects.create(strategy=s, dd_hard_halt_pct=Decimal("7.5"))
    _passing_backtest(s, oos="0.8", dd="4.0")
    out = validation_status(s)
    assert out["passed"] is True


def test_validation_fails_dd_over_limit(user):
    s = _strategy(user)
    StrategyAutopilot.objects.create(strategy=s, dd_hard_halt_pct=Decimal("7.5"))
    _passing_backtest(s, oos="0.8", dd="12.0")        # 12% > 7.5% limit
    out = validation_status(s)
    assert out["passed"] is False
    assert any(c["key"] == "drawdown_within_limit" and not c["ok"] for c in out["checks"])


def test_validation_stale_after_edit(user):
    s = _strategy(user)
    StrategyAutopilot.objects.create(strategy=s)
    _passing_backtest(s)
    s.max_position_pct = Decimal("0.03")
    s.save()                                          # bumps updated_at past the backtest
    out = validation_status(s)
    assert out["passed"] is False
    assert any(c["key"] == "backtest_fresh" and not c["ok"] for c in out["checks"])


# --------------------------------------------------------------------------
# Autopilot API — enable gate forces validation + auto_run_council.
# --------------------------------------------------------------------------
def test_enable_rejected_without_validation(client, user):
    s = _strategy(user)
    acc = _account(user)
    StrategyBrokerLink.objects.create(strategy=s, broker_account=acc)
    r = client.put(f"/api/strategies/{s.id}/autopilot/", {"is_enabled": True}, format="json")
    assert r.status_code == 409
    assert r.json()["validation"]["passed"] is False


def test_enable_succeeds_and_forces_council(client, user):
    s = _strategy(user, auto_run_council=False)
    acc = _account(user)
    StrategyBrokerLink.objects.create(strategy=s, broker_account=acc)
    StrategyAutopilot.objects.create(
        strategy=s, broker_account=acc, dd_hard_halt_pct=Decimal("7.5"),
    )
    _passing_backtest(s)
    r = client.post(f"/api/strategies/{s.id}/autopilot/enable/")
    assert r.status_code == 200
    s.refresh_from_db()
    assert s.auto_run_council is True                 # §6.0 forced on enable
    assert s.autopilot.is_enabled is True
    assert s.autopilot.next_run_at is not None


def test_save_rejects_invalid_model_preset(client, user):
    s = _strategy(user)
    StrategyAutopilot.objects.create(strategy=s)
    r = client.put(
        f"/api/strategies/{s.id}/autopilot/", {"model_preset": "frugel"}, format="json",
    )
    assert r.status_code == 400
    assert "model_preset" in r.json()["detail"]
    s.autopilot.refresh_from_db()
    assert s.autopilot.model_preset == "frugal"  # unchanged default


def test_save_accepts_valid_model_preset(client, user):
    s = _strategy(user)
    StrategyAutopilot.objects.create(strategy=s)
    r = client.put(
        f"/api/strategies/{s.id}/autopilot/", {"model_preset": "hybrid"}, format="json",
    )
    assert r.status_code == 200
    s.autopilot.refresh_from_db()
    assert s.autopilot.model_preset == "hybrid"


def test_disable_and_resume(client, user):
    s = _strategy(user)
    acc = _account(user)
    StrategyBrokerLink.objects.create(strategy=s, broker_account=acc)
    ap = StrategyAutopilot.objects.create(
        strategy=s, broker_account=acc, is_enabled=True,
        state=StrategyAutopilot.STATE_HALTED,
    )
    assert client.post(f"/api/strategies/{s.id}/autopilot/disable/").status_code == 200
    ap.refresh_from_db()
    assert ap.is_enabled is False
    # resume clears the halt state.
    r = client.post(f"/api/strategies/{s.id}/autopilot/resume/")
    assert r.status_code == 200
    ap.refresh_from_db()
    assert ap.state == StrategyAutopilot.STATE_ACTIVE


def test_account_resume_rebases_peak(client, user):
    """Per-account Resume is the same acknowledgment as the fund-level one:
    the stale peak rebases to the book's current equity so the breaker re-arms
    instead of re-halting on the next evaluation (the loop, per-account)."""
    s = _strategy(user)
    acc = _account(user)                                # $100k cash book
    StrategyBrokerLink.objects.create(strategy=s, broker_account=acc)
    ap = StrategyAutopilot.objects.create(
        strategy=s, broker_account=acc, is_enabled=True,
        state=StrategyAutopilot.STATE_HALTED,
        peak_equity_usd=Decimal("200000"),              # stale → dd 50%
    )
    r = client.post(f"/api/strategies/{s.id}/autopilot/resume/")
    assert r.status_code == 200
    ap.refresh_from_db()
    assert ap.state == StrategyAutopilot.STATE_ACTIVE
    assert ap.peak_equity_usd == Decimal("100000")      # re-armed at current equity
    assert r.json()["autopilot"]["peak_equity_usd"] == str(ap.peak_equity_usd)


def test_enable_out_of_halt_rebases_peak(client, user):
    """Disable→re-enable must not resurrect the stale peak: enabling a halted
    autopilot re-arms the breaker exactly like Resume."""
    s = _strategy(user)
    acc = _account(user)
    StrategyBrokerLink.objects.create(strategy=s, broker_account=acc)
    ap = StrategyAutopilot.objects.create(
        strategy=s, broker_account=acc,
        state=StrategyAutopilot.STATE_HALTED,
        peak_equity_usd=Decimal("200000"),
        dd_hard_halt_pct=Decimal("7.5"),
    )
    _passing_backtest(s)
    r = client.post(f"/api/strategies/{s.id}/autopilot/enable/")
    assert r.status_code == 200
    ap.refresh_from_db()
    assert ap.is_enabled is True
    assert ap.state == StrategyAutopilot.STATE_ACTIVE
    assert ap.peak_equity_usd == Decimal("100000")


def test_cron_edit_reschedules_enabled_autopilot(client, user):
    """Editing the cron on an already-enabled autopilot must recompute
    next_run_at — otherwise the new cadence persists but the next fire still
    points at the old schedule (regression: PUT skipped reschedule())."""
    s = _strategy(user)
    acc = _account(user)
    StrategyBrokerLink.objects.create(strategy=s, broker_account=acc)
    ap = StrategyAutopilot.objects.create(
        strategy=s, broker_account=acc, is_enabled=True,
        cron_expression="30 16 * * 5",            # Fridays 16:30
    )
    ap.reschedule()
    ap.save()
    before = ap.next_run_at
    assert before is not None
    r = client.put(
        f"/api/strategies/{s.id}/autopilot/",
        {"cron_expression": "0 9 * * 1-5"},        # weekdays 09:00
        format="json",
    )
    assert r.status_code == 200
    ap.refresh_from_db()
    assert ap.cron_expression == "0 9 * * 1-5"
    assert ap.next_run_at is not None
    assert ap.next_run_at != before                # rescheduled, not stale
    assert r.json()["autopilot"]["cron_description"]  # human-readable present


def test_put_rejects_invalid_cron(client, user):
    s = _strategy(user)
    StrategyAutopilot.objects.create(strategy=s)
    r = client.put(
        f"/api/strategies/{s.id}/autopilot/",
        {"cron_expression": "not a cron"}, format="json",
    )
    assert r.status_code == 400
    assert "cron_expression" in r.json()["detail"]


def test_executed_returns_broker_book(client, user):
    s = _strategy(user)
    acc = _account(user)
    StrategyBrokerLink.objects.create(strategy=s, broker_account=acc)
    r = client.get(f"/api/strategies/{s.id}/executed/")
    assert r.status_code == 200
    body = r.json()
    assert body["linked"] is True
    assert body["account_label"] == "A"


# --------------------------------------------------------------------------
# Fund layer + API.
# --------------------------------------------------------------------------
def _fund_of_three(user, *, cash="300000"):
    """P14 layout: ONE shared paper account, three member sleeves at an equal
    split, funded by Reset (33.33 / 33.33 / 33.34 % of the pool), all enabled."""
    fund = AutonomousFund.objects.create(owner=user, name="Autonomous Fund")
    acc = _account(user, label="POOL", cash=cash)
    sleeves.configure_account(fund, acc)
    members = []
    for i, pct in enumerate(sleeves.equal_split(3)):
        s = _strategy(user, name=f"S{i}")
        members.append({"strategy_id": s.id, "allocation_pct": str(pct)})
    sleeves.set_members(fund, members)
    sleeves.reset_fund(fund)
    StrategyAutopilot.objects.filter(strategy__in=fund.strategies.all()).update(is_enabled=True)
    fund.refresh_from_db()
    return fund


def test_fund_overview_aggregates_and_suppresses_correlation(user):
    fund = _fund_of_three(user)
    out = fund_layer.fund_overview(fund)
    assert len(out["members"]) == 3
    assert out["is_configured"] is True
    assert out["broker_account"]["label"] == "POOL"
    assert Decimal(out["aggregate_nav"]) == Decimal("300000")     # the ONE shared book
    # The pool is split by allocation: 33.33% / 33.33% / 33.34% of $300k.
    navs = sorted(Decimal(m["nav"]) for m in out["members"])
    assert navs == [Decimal("99990.00"), Decimal("99990.00"), Decimal("100020.00")]
    assert sum(navs) == Decimal("300000")
    assert Decimal(out["unallocated_nav"]) == Decimal("0")        # every dollar attributed
    assert out["allocation_total_pct"] == "100.00"
    assert out["correlation"]["available"] is False               # cold start
    assert out["correlation"]["reason"] == "insufficient_data"


def test_fund_overview_exposes_has_backtest(user):
    """phase-09a §8.2 — each card carries has_backtest (Run vs Re-run verb):
    False with no linked backtest, True once one exists (any status)."""
    fund = _fund_of_three(user)
    out = fund_layer.fund_overview(fund)
    assert all(p["has_backtest"] is False for p in out["members"])

    # Link a backtest (even a non-DONE one) to the first strategy.
    first = fund.strategies.order_by("id").first()
    Backtest.objects.create(
        user=user, strategy=first, name="bt",
        start_date=dt.date(2024, 1, 1), end_date=dt.date(2025, 1, 1),
        status=Backtest.QUEUED,
    )
    out2 = fund_layer.fund_overview(fund)
    by_id = {p["strategy_id"]: p for p in out2["members"]}
    assert by_id[first.id]["has_backtest"] is True
    assert sum(1 for p in out2["members"] if p["has_backtest"]) == 1


def test_fund_overview_is_live_tracks_enablement(user):
    """is_live = not halted AND ≥1 account enabled. A fund stays 'active' (not
    halted) even with every account disabled, but is_live must read False."""
    fund = _fund_of_three(user)                       # all 3 enabled
    assert fund_layer.fund_overview(fund)["is_live"] is True
    StrategyAutopilot.objects.filter(
        strategy__in=fund.strategies.all(),
    ).update(is_enabled=False)
    out = fund_layer.fund_overview(fund)
    assert out["state"] == "active"                   # still not halted
    assert out["is_live"] is False                    # but nothing trades
    assert all("cron_description" in p for p in out["members"])


def test_fund_halt_halts_all_then_resume(user):
    fund = _fund_of_three(user)
    n = fund_layer.halt_fund(fund)
    assert n == 3
    fund.refresh_from_db()
    assert fund.state == AutonomousFund.STATE_HALTED
    assert all(
        ap.state == StrategyAutopilot.STATE_HALTED
        for ap in StrategyAutopilot.objects.all()
    )
    out = fund_layer.resume_fund(fund)
    fund.refresh_from_db()
    assert fund.state == AutonomousFund.STATE_ACTIVE
    # Full restart: every member account is un-halted with its breaker
    # re-armed (an un-cleared account would just idle behind a stale peak).
    assert out["accounts_resumed"] == 3
    assert all(
        ap.state == StrategyAutopilot.STATE_ACTIVE
        for ap in StrategyAutopilot.objects.all()
    )


def test_fund_resume_rebases_peaks_and_breaks_the_rehalt_loop(user):
    """The escape hatch for the halt loop: a halted fund can't trade, so its
    drawdown vs. a stale all-time peak can never shrink — clearing the halt
    without rebasing the peak just re-halts on the next guardrail sweep.
    Resume must rebase fund + account peaks to current equity (drawdown 0)."""
    fund = _fund_of_three(user)                        # one $300k book, 3 sleeves
    fund.fund_dd_halt_pct = Decimal("20")
    fund.peak_equity_usd = Decimal("400000")           # stale peak → dd 25%
    fund.save()
    res = fund_layer.evaluate_fund_drawdown(fund)
    assert res["halted"] is True                       # 25% ≥ 20% → breaker fires
    fund.refresh_from_db()
    assert fund.state == AutonomousFund.STATE_HALTED

    StrategyAutopilot.objects.all().update(peak_equity_usd=Decimal("150000"))
    out = fund_layer.resume_fund(fund)
    fund.refresh_from_db()
    assert fund.state == AutonomousFund.STATE_ACTIVE
    assert fund.peak_equity_usd == Decimal("300000")   # rebased to the account's equity
    assert out["peak_rebased_to"] == str(fund.peak_equity_usd)
    for ap in StrategyAutopilot.objects.select_related("strategy__fund_sleeve__portfolio"):
        assert ap.state == StrategyAutopilot.STATE_ACTIVE
        # each sleeve's OWN equity (its share of the pool), not the account's
        assert ap.peak_equity_usd == ap.strategy.fund_sleeve.portfolio.cash_balance

    # The next sweep evaluation is clean — no immediate re-halt (loop broken).
    res2 = fund_layer.evaluate_fund_drawdown(fund)
    assert res2["halted"] is False
    assert float(res2["drawdown_pct"]) == 0.0
    fund.refresh_from_db()
    assert fund.state == AutonomousFund.STATE_ACTIVE


def test_fund_resume_without_valuable_equity_clears_peak_for_reseed(user):
    """No members / unpriceable books: resume clears the peak instead of
    rebasing, so the next evaluation cold-start re-seeds (drawdown 0)."""
    fund = AutonomousFund.objects.create(
        owner=user, name="Empty", state=AutonomousFund.STATE_HALTED,
        peak_equity_usd=Decimal("500000"),
    )
    out = fund_layer.resume_fund(fund)
    fund.refresh_from_db()
    assert fund.state == AutonomousFund.STATE_ACTIVE
    assert fund.peak_equity_usd is None
    assert out["peak_rebased_to"] is None


def test_fund_overview_reports_drawdown_pct(user):
    fund = _fund_of_three(user)
    fund.peak_equity_usd = None                        # (reset seeded it) → no peak yet
    fund.save()
    assert fund_layer.fund_overview(fund)["drawdown_pct"] is None
    fund.peak_equity_usd = Decimal("400000")           # the book is $300k → 25%
    fund.save()
    assert fund_layer.fund_overview(fund)["drawdown_pct"] == 25.0


def test_fund_api_halt_resume(client, user):
    _fund_of_three(user)
    assert client.get("/api/fund/").status_code == 200
    r = client.post("/api/fund/halt/")
    assert r.status_code == 200
    assert r.json()["accounts_halted"] == 3
    body = client.post("/api/fund/resume/").json()
    assert body["state"] == "active"
    assert body["accounts_resumed"] == 3


# --------------------------------------------------------------------------
# Notifications — carry the disclaimer.
# --------------------------------------------------------------------------
def test_notify_autopilot_creates_event_with_disclaimer(user):
    from apps.notifications.autopilot import DISCLAIMER, FILL, notify_autopilot

    s = _strategy(user)
    channel = NotificationChannel.objects.create(
        user=user, kind=NotificationChannel.EMAIL, config={"address": "x@y.test"},
    )
    ap = StrategyAutopilot.objects.create(strategy=s, notification_channel=channel)
    assert notify_autopilot(ap, FILL, "AAPL filled 10 @ 200") is True
    ev = NotificationEvent.objects.filter(channel=channel).first()
    assert ev is not None
    assert DISCLAIMER in ev.body
