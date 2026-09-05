"""P14 — the shared-pool autonomous fund: N strategies trade ONE paper account
through per-strategy sleeves.

Covers: sleeve-isolated sizing + fills attribution on the shared book, the
roster/allocation writer (validation, funding from unallocated cash, removal
rules), reset (fresh start requires a flat account), flatten, the Fund-tab API
surface, the wash-trade release deferral, and the 0037 data migration.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from rest_framework.test import APIClient

from apps.brokers import demo_fills
from apps.brokers.adapters import mock as mock_adapter
from apps.brokers.models import BrokerAccount, BrokerOrder, StrategyBrokerLink
from apps.portfolios import autopilot as bridge
from apps.portfolios import fund as fund_layer
from apps.portfolios import sleeves, tasks_autopilot
from apps.portfolios.models import (
    AutonomousFund,
    FundSleeve,
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    RebalanceOrder,
    StrategyAutopilot,
    Universe,
    UniverseMembership,
)

User = get_user_model()


@pytest.fixture(autouse=True)
def _reset_mock():
    mock_adapter.reset_state()
    yield
    mock_adapter.reset_state()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="p14@x.test", password="pw-fake-123456789")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture
def price_200(monkeypatch):
    monkeypatch.setattr(demo_fills, "live_price", lambda account, ticker: Decimal("200"))


def _strategy(user, name="S", **kw):
    u = Universe.objects.create(name=f"p14-uni-{name}")
    for t in ("AAPL", "MSFT", "XLE"):
        UniverseMembership.objects.create(
            universe=u, ticker=t, sector="Tech", effective_from=dt.date(2020, 1, 1),
        )
    pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_STRATEGY, name=name)
    defaults = dict(
        kind=PortfolioStrategy.KIND_LONG_SHORT,
        max_position_pct=Decimal("0.10"),
        max_sector_pct=Decimal("0.50"),
        min_trade_notional_usd=Decimal("100"),
        max_turnover_pct=Decimal("1.0"),
        auto_run_council=True,
    )
    defaults.update(kw)
    return PortfolioStrategy.objects.create(
        user=user, name=name, universe=u, portfolio=pf, **defaults,
    )


def _account(user, *, broker="mock", cash="100000", label="POOL", mode=BrokerAccount.MODE_PAPER):
    pf = Portfolio.objects.create(
        user=user, kind=Portfolio.KIND_BROKER, name=f"bk-{label}", cash_balance=Decimal(cash),
    )
    acc = BrokerAccount.objects.create(
        user=user, broker=broker, mode=mode, account_id=f"{broker}-{label}-{user.id}",
        label=label, portfolio=pf, connection_status=BrokerAccount.STATUS_ACTIVE,
    )
    if broker == "mock":
        mock_adapter.seed_demo_book(acc, cash=Decimal(cash))
    return acc


def _fund(user, allocations, *, cash="100000", broker="mock", enable=True):
    """A configured fund on one ``broker`` account with members at ``allocations``
    (name → pct), funded by reset. Returns (fund, {name: strategy})."""
    fund = AutonomousFund.objects.create(owner=user, name="Autonomous Fund")
    sleeves.configure_account(fund, _account(user, broker=broker, cash=cash))
    strategies = {name: _strategy(user, name=name) for name in allocations}
    sleeves.set_members(fund, [
        {"strategy_id": strategies[n].id, "allocation_pct": str(p)} for n, p in allocations.items()
    ])
    sleeves.reset_fund(fund)
    if enable:
        StrategyAutopilot.objects.filter(strategy__in=strategies.values()).update(is_enabled=True)
    fund.refresh_from_db()
    return fund, strategies


def _done_target(strategy, weights):
    # One target per (strategy, day): each simulated cycle is a new trading day.
    n = PortfolioTarget.objects.filter(strategy=strategy).count()
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=dt.date(2026, 9, 4) + dt.timedelta(days=n),
        status=PortfolioTarget.DONE, target_weights=weights,
    )
    for t in weights:
        RebalanceOrder.objects.create(
            target=target, ticker=t, side="buy", quantity=Decimal("1"),
            limit_price=Decimal("200"), reason="open",
            estimated_notional_usd=Decimal("200"), sequence=2,
        )
    return target


def _cycle(strategy, weights):
    """Run the bridge for one finished cycle of ``strategy`` (sleeve-resolved)."""
    ap = strategy.autopilot
    return bridge.maybe_emit_and_submit(_done_target(strategy, weights), autopilot=ap)


def _positions(book):
    return {p.ticker: Decimal(str(p.quantity)) for p in book.positions.all()}


# --------------------------------------------------------------------------
# Sleeves: sizing off the sleeve's own capital, fills attributed, siblings
# untouched — the property that makes one account shareable.
# --------------------------------------------------------------------------
def test_two_sleeves_share_one_account_with_isolated_sizing(user, price_200):
    fund, s = _fund(user, {"A": 60, "B": 40})
    acct = fund.broker_account.portfolio
    slA, slB = s["A"].fund_sleeve, s["B"].fund_sleeve
    assert slA.portfolio.cash_balance == Decimal("60000.00")
    assert slB.portfolio.cash_balance == Decimal("40000.00")
    assert slA.initial_capital_usd == Decimal("60000.00")

    # A: 10% AAPL of ITS $60k sleeve → 30 shares @200 (not 10% of the $100k account).
    dec = _cycle(s["A"], {"AAPL": 0.10})
    assert dec["submitted"] == 1 and dec["sleeve"] == slA.id
    order = BrokerOrder.objects.get(broker_account=fund.broker_account)
    assert order.sleeve_id == slA.id
    assert order.status == BrokerOrder.STATUS_FILLED
    assert Decimal(str(order.quantity)) == Decimal("30")
    for book in (slA.portfolio, slB.portfolio, acct):
        book.refresh_from_db()
    assert _positions(slA.portfolio) == {"AAPL": Decimal("30")}
    assert slA.portfolio.cash_balance == Decimal("54000.00")
    assert _positions(slB.portfolio) == {}                       # sibling untouched
    assert slB.portfolio.cash_balance == Decimal("40000.00")
    assert _positions(acct) == {"AAPL": Decimal("30")}           # the account = Σ sleeves
    assert acct.cash_balance == Decimal("94000.00")

    # B: 10% MSFT of ITS $40k → 20 shares; A's AAPL is NOT sold by B's rebalance.
    _cycle(s["B"], {"MSFT": 0.10})
    acct.refresh_from_db()
    assert _positions(acct) == {"AAPL": Decimal("30"), "MSFT": Decimal("20")}
    assert _positions(s["B"].fund_sleeve.portfolio) == {"MSFT": Decimal("20")}
    assert _positions(s["A"].fund_sleeve.portfolio) == {"AAPL": Decimal("30")}

    # A goes to cash: only A's AAPL is closed; B's MSFT stays in the account.
    _cycle(s["A"], {})
    acct.refresh_from_db()
    assert _positions(acct) == {"MSFT": Decimal("20")}
    assert _positions(s["A"].fund_sleeve.portfolio) == {}
    a_book = Portfolio.objects.get(pk=s["A"].fund_sleeve.portfolio_id)
    assert a_book.cash_balance == Decimal("60000.00")               # round trip @200

    # Attribution is exact: Σ sleeves == account, nothing unallocated.
    out = fund_layer.fund_overview(fund)
    assert out["is_configured"] is True
    assert Decimal(out["aggregate_nav"]) == Decimal(out["broker_account"]["nav"])
    assert Decimal(out["unallocated_nav"]) == Decimal("0.00")
    assert out["attribution_gap"] == {"available": True, "positions": {}, "cash": "0.00"}
    by = {m["name"]: m for m in out["members"]}
    assert by["A"]["allocation_pct"] == "60.00" and by["B"]["allocation_pct"] == "40.00"
    assert by["A"]["initial_capital"] == "60000.00"
    assert out["members_count"] == 2


def test_daily_caps_count_only_the_sleeves_own_orders(user, price_200):
    fund, s = _fund(user, {"A": 50, "B": 50})
    _cycle(s["A"], {"AAPL": 0.05, "MSFT": 0.05})
    run = s["A"].autopilot.runs.create(fire_time_utc=timezone.now())
    run.broker_orders.add(*BrokerOrder.objects.filter(sleeve=s["A"].fund_sleeve))
    acct = fund.broker_account
    n_all, _ = bridge._prior_24h(acct)
    n_a, _ = bridge._prior_24h(acct, sleeve=s["A"].fund_sleeve)
    n_b, _ = bridge._prior_24h(acct, sleeve=s["B"].fund_sleeve)
    assert n_all == 2 and n_a == 2 and n_b == 0


def test_risk_cap_measured_against_the_sleeve_not_the_account(user, price_200):
    """max_position_pct binds on the SLEEVE's NAV: a name worth 10% of the
    account is 25% of a 40% sleeve and must be rejected there."""
    fund, s = _fund(user, {"A": 60, "B": 40})
    acct = fund.broker_account
    order = BrokerOrder.objects.create(
        broker_account=acct, ticker="AAPL", side="buy",
        quantity=Decimal("50"), limit_price=Decimal("200"),   # $10k
    )
    against_sleeve = sleeves.execution_context(s["B"])
    check = __import__("apps.portfolios.autopilot_risk", fromlist=["x"]).make_risk_check(
        s["B"], book=against_sleeve.book,
    )
    assert check(order)                                   # 25% of $40k > 10% cap
    whole = __import__("apps.portfolios.autopilot_risk", fromlist=["x"]).make_risk_check(
        s["B"], book=acct.portfolio,
    )
    assert whole(order) == []                            # 10% of $100k would have passed


def test_unfunded_sleeve_trades_nothing(user, price_200):
    """A member of a configured fund whose sleeve has no capital yet (no Reset)
    must not size off a phantom $100k."""
    fund = AutonomousFund.objects.create(owner=user, name="F")
    sleeves.configure_account(fund, _account(user))
    s = _strategy(user, name="A")
    sleeves.set_members(fund, [{"strategy_id": s.id, "allocation_pct": "100"}])
    # Joined with unallocated cash → funded; withdraw it to model "not reset yet".
    sl = s.fund_sleeve
    sl.portfolio.cash_balance = Decimal("0")
    sl.portfolio.save()
    sl.initial_capital_usd = Decimal("0")
    sl.save()
    StrategyAutopilot.objects.filter(strategy=s).update(is_enabled=True)
    dec = _cycle(s, {"AAPL": 0.10})
    assert dec["orders"] == 0
    assert BrokerOrder.objects.count() == 0


def test_unconfigured_fund_member_has_no_execution_context(user):
    fund = AutonomousFund.objects.create(owner=user, name="F")
    s = _strategy(user, name="A")
    sleeves.create_sleeve(fund, s, 100)
    assert sleeves.execution_context(s) is None
    assert sleeves.member_book(s) == s.fund_sleeve.portfolio      # the (empty) sleeve
    out = fund_layer.fund_overview(fund)
    assert out["is_configured"] is False
    assert out["broker_account"] is None
    assert out["reset"]["reason"] == "no_account"
    assert "account" in out["members"][0]["setup_hint"].lower()


# --------------------------------------------------------------------------
# Roster + allocations.
# --------------------------------------------------------------------------
def test_set_members_validates_allocations(user):
    fund = AutonomousFund.objects.create(owner=user, name="F")
    a, b = _strategy(user, "A"), _strategy(user, "B")
    with pytest.raises(sleeves.FundError) as exc:
        sleeves.set_members(fund, [
            {"strategy_id": a.id, "allocation_pct": 60},
            {"strategy_id": b.id, "allocation_pct": 30},
        ])
    assert exc.value.status == 400 and "100%" in exc.value.detail
    with pytest.raises(sleeves.FundError, match="twice"):
        sleeves.set_members(fund, [
            {"strategy_id": a.id, "allocation_pct": 50},
            {"strategy_id": a.id, "allocation_pct": 50},
        ])
    other = User.objects.create_user(email="o@x.test", password="pw-fake-123456789")
    theirs = _strategy(other, "T")
    with pytest.raises(sleeves.FundError) as exc:
        sleeves.set_members(fund, [{"strategy_id": theirs.id, "allocation_pct": 100}])
    assert exc.value.status == 404
    # A hair of rounding is fine; an empty roster is fine.
    sleeves.set_members(fund, [
        {"strategy_id": a.id, "allocation_pct": "33.33"},
        {"strategy_id": b.id, "allocation_pct": "66.66"},
    ])
    assert fund.sleeves.filter(is_active=True).count() == 2
    sleeves.set_members(fund, [])
    assert fund.sleeves.filter(is_active=True).count() == 0
    assert fund.sleeves.count() == 2                             # ledgers kept (inactive)


def test_equal_split_sums_to_100():
    assert sum(sleeves.equal_split(3)) == Decimal("100")
    assert sleeves.equal_split(3) == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]
    assert sleeves.equal_split(1) == [Decimal("100")]
    assert sleeves.equal_split(0) == []


def test_new_member_funded_from_unallocated_cash_and_removal_returns_it(user):
    fund, s = _fund(user, {"A": 50, "B": 50}, enable=False)
    assert sleeves.unallocated_cash(fund) == Decimal("0.00")
    # Remove B (flat): its $50k goes back to the pool — A keeps its own slice.
    sleeves.set_members(fund, [{"strategy_id": s["A"].id, "allocation_pct": "100"}])
    b = FundSleeve.objects.get(strategy=s["B"])
    assert b.is_active is False and b.removed_at is not None
    assert b.portfolio.cash_balance == Decimal("0.00")
    assert not StrategyBrokerLink.objects.filter(strategy=s["B"], is_active=True).exists()
    assert sleeves.unallocated_cash(fund) == Decimal("50000.00")
    assert s["A"].fund_sleeve.portfolio.cash_balance == Decimal("50000.00")   # slices float
    # Add C at 50%: funded from the UNALLOCATED $50k, not from A.
    c = _strategy(user, "C")
    summary = sleeves.set_members(fund, [
        {"strategy_id": s["A"].id, "allocation_pct": "50"},
        {"strategy_id": c.id, "allocation_pct": "50"},
    ])
    assert summary["added"] == [c.id] and summary["warnings"] == []
    assert c.fund_sleeve.portfolio.cash_balance == Decimal("50000.00")
    assert c.fund_sleeve.initial_capital_usd == Decimal("50000.00")
    assert sleeves.unallocated_cash(fund) == Decimal("0.00")
    # Autopilot + link were bound to the pool account, disabled, on a free slot.
    ap = c.autopilot
    assert ap.is_enabled is False and ap.broker_account_id == fund.broker_account_id
    assert ap.cron_expression != s["A"].autopilot.cron_expression
    # Nothing left to allocate → the next newcomer starts at $0 with a warning.
    d = _strategy(user, "D")
    summary = sleeves.set_members(fund, [
        {"strategy_id": s["A"].id, "allocation_pct": "40"},
        {"strategy_id": c.id, "allocation_pct": "40"},
        {"strategy_id": d.id, "allocation_pct": "20"},
    ])
    assert d.fund_sleeve.portfolio.cash_balance == Decimal("0.00")
    assert any("Reset" in w for w in summary["warnings"])
    # Re-adding B re-activates its ORIGINAL sleeve (one ledger per strategy).
    sleeves.set_members(fund, [
        {"strategy_id": s["A"].id, "allocation_pct": "50"},
        {"strategy_id": s["B"].id, "allocation_pct": "50"},
    ])
    assert FundSleeve.objects.filter(strategy=s["B"]).count() == 1
    assert FundSleeve.objects.get(strategy=s["B"]).is_active is True


def test_remove_member_holding_positions_needs_force_flatten(user, price_200):
    fund, s = _fund(user, {"A": 50, "B": 50})
    _cycle(s["A"], {"AAPL": 0.10})                       # A holds 25 AAPL
    with pytest.raises(sleeves.FundError) as exc:
        sleeves.set_members(fund, [{"strategy_id": s["B"].id, "allocation_pct": "100"}])
    assert exc.value.status == 409
    assert exc.value.extra["blocking"][0]["positions"] == ["AAPL"]
    summary = sleeves.set_members(
        fund, [{"strategy_id": s["B"].id, "allocation_pct": "100"}], force_flatten=True,
    )
    assert summary["flattening"] == [{"strategy_id": s["A"].id, "orders": 1}]
    close = BrokerOrder.objects.filter(sleeve=s["A"].fund_sleeve).order_by("-id").first()
    assert close.side == "sell" and close.status == BrokerOrder.STATUS_FILLED   # demo fills
    a = FundSleeve.objects.get(strategy=s["A"])
    assert a.is_active is False
    assert _positions(a.portfolio) == {}                  # the close attributed to A
    assert _positions(fund.broker_account.portfolio) == {}
    assert StrategyAutopilot.objects.get(strategy=s["A"]).is_enabled is False
    # Its leftover cash is simply unallocated now (returned to the pool view).
    assert sleeves.unallocated_cash(fund) == a.portfolio.cash_balance


# --------------------------------------------------------------------------
# Account, reset, flatten.
# --------------------------------------------------------------------------
def test_configure_account_rejects_live_and_foreign_accounts(user):
    fund = AutonomousFund.objects.create(owner=user, name="F")
    live = _account(user, broker="alpaca_paper", label="LIVE", mode=BrokerAccount.MODE_LIVE)
    with pytest.raises(sleeves.FundError) as exc:
        sleeves.configure_account(fund, live)
    assert exc.value.status == 400
    other = User.objects.create_user(email="o2@x.test", password="pw-fake-123456789")
    with pytest.raises(sleeves.FundError) as exc:
        sleeves.configure_account(fund, _account(other, label="THEIRS"))
    assert exc.value.status == 404


def test_change_account_requires_flat_sleeves_and_zeroes_them(user, price_200):
    fund, s = _fund(user, {"A": 100})
    _cycle(s["A"], {"AAPL": 0.10})
    second = _account(user, label="POOL2")
    with pytest.raises(sleeves.FundError, match="flatten"):
        sleeves.configure_account(fund, second)
    _cycle(s["A"], {})                                   # back to cash
    res = sleeves.configure_account(fund, second)
    assert res["changed"] is True
    fund.refresh_from_db()
    assert fund.broker_account_id == second.id
    sl = s["A"].fund_sleeve
    assert sl.portfolio.cash_balance == Decimal("0.00")  # meaningless vs the new account
    link = StrategyBrokerLink.objects.get(strategy=s["A"], is_active=True)
    assert link.broker_account_id == second.id
    assert StrategyAutopilot.objects.get(strategy=s["A"]).broker_account_id == second.id
    assert fund_layer.fund_overview(fund)["reset"]["ready"] is True


def test_reset_requires_flat_account_then_refunds_by_allocation(user, price_200):
    fund, s = _fund(user, {"A": 70, "B": 30})
    _cycle(s["A"], {"AAPL": 0.10})
    ready = sleeves.reset_readiness(fund)
    assert ready == {"ready": False, "reason": "positions", "positions": 1, "inflight_orders": 0}
    with pytest.raises(sleeves.FundError) as exc:
        sleeves.reset_fund(fund)
    assert exc.value.status == 409 and exc.value.extra["reason"] == "positions"

    res = sleeves.flatten_fund(fund)
    assert res["orders"] == 1
    close = BrokerOrder.objects.filter(client_order_id__startswith="flat-f").get()
    assert close.sleeve_id == s["A"].fund_sleeve.id and close.status == BrokerOrder.STATUS_FILLED
    assert sleeves.reset_readiness(fund)["ready"] is True

    # Change the split, then reset: the account's cash is re-cut 50/50.
    sleeves.set_members(fund, [
        {"strategy_id": s["A"].id, "allocation_pct": "50"},
        {"strategy_id": s["B"].id, "allocation_pct": "50"},
    ])
    fund.state = AutonomousFund.STATE_HALTED
    fund.save()
    StrategyAutopilot.objects.update(state=StrategyAutopilot.STATE_HALTED)
    out = sleeves.reset_fund(fund)
    assert out["account_cash"] == "100000.00"
    fund.refresh_from_db()
    assert fund.state == AutonomousFund.STATE_ACTIVE
    assert fund.peak_equity_usd == Decimal("100000.00")
    for name in ("A", "B"):
        sl = FundSleeve.objects.get(strategy=s[name])
        assert sl.portfolio.cash_balance == Decimal("50000.00")
        assert sl.initial_capital_usd == Decimal("50000.00")
        ap = s[name].autopilot
        ap.refresh_from_db()
        assert ap.state == StrategyAutopilot.STATE_ACTIVE
        assert ap.peak_equity_usd == Decimal("50000.00")
        assert ap.next_run_at is not None                 # enabled members are rescheduled
    # The reset is booked as an external flow on the sleeve ledgers (TWR-neutral).
    ledger = s["A"].fund_sleeve.portfolio.ledger.order_by("-id").first()
    assert ledger.kind in ("deposit", "withdrawal") and "fund reset" in ledger.note


def test_flatten_fund_attributes_closes_per_sleeve_and_residual(user, price_200):
    fund, s = _fund(user, {"A": 50, "B": 50})
    _cycle(s["A"], {"AAPL": 0.10})                       # A: 25 AAPL
    _cycle(s["B"], {"AAPL": 0.10})                       # B: 25 AAPL (same name!)
    acct = fund.broker_account.portfolio
    # A manual/legacy position nobody's sleeve claims.
    from apps.portfolios.models import Position

    Position.objects.create(
        portfolio=acct, ticker="XLE", quantity=Decimal("10"), avg_cost=Decimal("200"),
    )
    res = sleeves.flatten_fund(fund)
    assert res["orders"] == 3
    closes = BrokerOrder.objects.filter(client_order_id__startswith="flat-f")
    by_sleeve = {c.sleeve_id: Decimal(str(c.quantity)) for c in closes if c.sleeve_id}
    assert by_sleeve == {s["A"].fund_sleeve.id: Decimal("25"), s["B"].fund_sleeve.id: Decimal("25")}
    residual = closes.get(sleeve__isnull=True)
    assert residual.ticker == "XLE" and Decimal(str(residual.quantity)) == Decimal("10")
    acct.refresh_from_db()
    assert _positions(acct) == {}
    assert _positions(s["A"].fund_sleeve.portfolio) == {}
    assert _positions(s["B"].fund_sleeve.portfolio) == {}


def test_rolling_stats_start_at_the_sleeves_last_funding(user, price_200):
    """Production 2026-09-05: right after the cut-over both pods showed a rolling
    Sharpe of about −2.3 and a 1.00 pairwise correlation. Their AutopilotRun
    equity series chained the retired $100k-account era straight into the new
    ~$50k sleeves, so the Reset's funding step read as a −50% weekly return.
    The series must start at the sleeve's last external cash flow."""
    from apps.portfolios.models import AutopilotRun

    fund, s = _fund(user, {"A": 50, "B": 50})
    reset_at = timezone.now()
    for name in ("A", "B"):
        ap = s[name].autopilot
        # Old-account era: two cycles on a $100k book, before the reset.
        for weeks, eq in ((3, "100000"), (2, "100500")):
            AutopilotRun.objects.create(
                autopilot=ap, fire_time_utc=reset_at - dt.timedelta(weeks=weeks),
                status=AutopilotRun.SUBMITTED,
                guardrail_actions={"drawdown": {"equity": eq}},
            )
        # Sleeve era: two cycles after the reset on the ~$50k slice.
        for days, eq in ((1, "50000"), (8, "50250" if name == "A" else "49900")):
            AutopilotRun.objects.create(
                autopilot=ap, fire_time_utc=reset_at + dt.timedelta(days=days),
                status=AutopilotRun.SUBMITTED,
                guardrail_actions={"drawdown": {"equity": eq}},
            )
    assert fund_layer._equity_series(s["A"]) == [50000.0, 50250.0]   # no $100k → $50k step
    assert fund_layer._equity_series(s["B"]) == [50000.0, 49900.0]
    out = fund_layer.fund_overview(fund)
    by = {m["name"]: m for m in out["members"]}
    # One post-reset return each: too short for a Sharpe (needs ≥ 2) — and
    # certainly not the −2.3 the funding step used to fabricate.
    assert by["A"]["rolling_sharpe"] is None and by["B"]["rolling_sharpe"] is None
    assert out["recommendations"] == []
    # A strategy with no sleeve (legacy link) keeps its whole history.
    legacy = _strategy(user, "L")
    acc = _account(user, label="LEGACY")
    StrategyBrokerLink.objects.create(strategy=legacy, broker_account=acc)
    ap = StrategyAutopilot.objects.create(strategy=legacy, broker_account=acc, is_enabled=True)
    for weeks, eq in ((2, "100000"), (1, "101000")):
        AutopilotRun.objects.create(
            autopilot=ap, fire_time_utc=reset_at - dt.timedelta(weeks=weeks),
            status=AutopilotRun.SUBMITTED, guardrail_actions={"drawdown": {"equity": eq}},
        )
    assert fund_layer._equity_series(legacy) == [100000.0, 101000.0]


def test_fund_drawdown_and_resume_use_the_account_and_sleeves(user, price_200):
    fund, s = _fund(user, {"A": 50, "B": 50})
    fund.fund_dd_halt_pct = Decimal("6")
    fund.peak_equity_usd = Decimal("110000")             # stale → 9.09% dd on a $100k book
    fund.save()
    res = fund_layer.evaluate_fund_drawdown(fund)
    assert res["halted"] is True and Decimal(res["equity"]) == Decimal("100000.00")
    n_halted = StrategyAutopilot.objects.filter(state=StrategyAutopilot.STATE_HALTED).count()
    assert n_halted == 2
    out = fund_layer.resume_fund(fund)
    assert out["peak_rebased_to"] == "100000.00" and out["accounts_resumed"] == 2
    for name in ("A", "B"):
        ap = s[name].autopilot
        ap.refresh_from_db()
        assert ap.peak_equity_usd == Decimal("50000.00")   # each sleeve's OWN equity


# --------------------------------------------------------------------------
# Fund-tab API.
# --------------------------------------------------------------------------
def test_fund_api_setup_roster_reset_flow(client, user, price_200):
    acc = _account(user)
    a, b = _strategy(user, "A"), _strategy(user, "B")
    # No fund yet.
    assert client.get("/api/fund/").json() == {"fund": None}
    r = client.get("/api/fund/accounts/")
    assert r.status_code == 200 and r.json()["accounts"][0]["in_fund"] is False
    # Create + choose the shared paper account.
    r = client.put(
        "/api/fund/", {"broker_account_id": acc.id, "fund_dd_halt_pct": "8"}, format="json",
    )
    assert r.status_code == 201, r.json()
    body = r.json()
    assert body["created"] is True and body["is_configured"] is True
    assert body["broker_account"]["id"] == acc.id and body["fund_dd_halt_pct"] == "8.00"
    assert body["reset"]["reason"] == "no_members"
    # Candidates list the user's strategies with membership + gate state.
    cands = client.get("/api/fund/candidates/").json()["strategies"]
    assert {c["id"] for c in cands} == {a.id, b.id}
    assert all(c["is_member"] is False and c["validation_passed"] is False for c in cands)
    # Roster: bad total → 400; good → members funded from the pool (join = unallocated cash).
    r = client.put("/api/fund/members/", {"members": [
        {"strategy_id": a.id, "allocation_pct": 50}, {"strategy_id": b.id, "allocation_pct": 40},
    ]}, format="json")
    assert r.status_code == 400
    r = client.put("/api/fund/members/", {"members": [
        {"strategy_id": a.id, "allocation_pct": 50}, {"strategy_id": b.id, "allocation_pct": 50},
    ]}, format="json")
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["changes"]["added"] == [a.id, b.id]
    assert {m["allocation_pct"] for m in body["members"]} == {"50.00"}
    assert {m["cash"] for m in body["members"]} == {"50000.00"}
    assert body["reset"]["ready"] is True
    # Reset (fresh start) is allowed on a flat account.
    r = client.post("/api/fund/reset/")
    assert r.status_code == 200 and r.json()["reset"]["account_cash"] == "100000.00"
    # Enable A → a cycle → reset now refused (positions) → flatten → reset OK.
    StrategyAutopilot.objects.filter(strategy=a).update(is_enabled=True)
    _cycle(a, {"AAPL": 0.10})
    r = client.post("/api/fund/reset/")
    assert r.status_code == 409 and r.json()["reason"] == "positions"
    r = client.post("/api/fund/flatten/")
    assert r.status_code == 200 and r.json()["flatten"]["orders"] == 1
    r = client.post("/api/fund/reset/")
    assert r.status_code == 200
    # The kill switch reports the member count the Fund tab shows.
    r = client.post("/api/fund/halt/")
    assert r.json()["accounts_halted"] == 2
    assert client.get("/api/fund/").json()["members_count"] == 2
    # Sleeves are flat again → the shared account may be swapped.
    other_acc = _account(user, label="POOL2")
    r = client.put("/api/fund/", {"broker_account_id": other_acc.id}, format="json")
    assert r.status_code == 200 and r.json()["broker_account"]["id"] == other_acc.id
    # Executed-book view shows the SLEEVE (not the whole account).
    r = client.get(f"/api/strategies/{a.id}/executed/")
    assert r.status_code == 200 and r.json()["is_sleeve"] is True
    assert r.json()["account_label"] == "POOL2"


def test_fund_api_members_remove_with_positions_409_then_force(client, user, price_200):
    fund, s = _fund(user, {"A": 50, "B": 50})
    _cycle(s["A"], {"AAPL": 0.10})
    r = client.put("/api/fund/members/", {"members": [
        {"strategy_id": s["B"].id, "allocation_pct": 100},
    ]}, format="json")
    assert r.status_code == 409
    assert r.json()["blocking"][0]["strategy_id"] == s["A"].id
    r = client.put("/api/fund/members/", {
        "members": [{"strategy_id": s["B"].id, "allocation_pct": 100}], "force_flatten": True,
    }, format="json")
    assert r.status_code == 200
    assert r.json()["members_count"] == 1
    assert r.json()["leaving"] == []                     # demo fill closed it already


def test_enable_refused_until_fund_has_an_account(client, user):
    fund = AutonomousFund.objects.create(owner=user, name="F")
    s = _strategy(user, "A")
    sleeves.create_sleeve(fund, s, 100)
    from tests.test_p7_fund_flow import _real_validation_backtest

    StrategyAutopilot.objects.create(strategy=s, dd_hard_halt_pct=Decimal("7.5"))
    _real_validation_backtest(s)
    r = client.post(f"/api/strategies/{s.id}/autopilot/enable/")
    assert r.status_code == 409 and "account" in r.json()["detail"]
    sleeves.configure_account(fund, _account(user))
    r = client.post(f"/api/strategies/{s.id}/autopilot/enable/")
    assert r.status_code == 200
    assert r.json()["autopilot"]["sleeve"]["fund_configured"] is True


# --------------------------------------------------------------------------
# Release: one order per (account, ticker) per tick on the shared account.
# --------------------------------------------------------------------------
def test_release_defers_second_same_ticker_order_until_the_first_settles(user, monkeypatch):
    acc = _account(user, broker="alpaca_paper")
    past = timezone.now() - dt.timedelta(minutes=1)
    o1 = BrokerOrder.objects.create(
        broker_account=acc, ticker="XLE", side="buy", quantity=Decimal("5"),
        status=BrokerOrder.STATUS_PENDING_OPEN, release_after=past,
    )
    o2 = BrokerOrder.objects.create(
        broker_account=acc, ticker="XLE", side="sell", quantity=Decimal("3"),
        status=BrokerOrder.STATUS_PENDING_OPEN, release_after=past,
    )
    o3 = BrokerOrder.objects.create(
        broker_account=acc, ticker="MSFT", side="buy", quantity=Decimal("1"),
        status=BrokerOrder.STATUS_PENDING_OPEN, release_after=past,
    )
    monkeypatch.setattr("apps.brokers.market_calendar.is_market_open", lambda *a, **k: True)
    submitted = []

    def fake_submit(order):
        submitted.append(order.id)
        BrokerOrder.objects.filter(pk=order.pk).update(status=BrokerOrder.STATUS_SUBMITTED)
        return True

    monkeypatch.setattr("apps.portfolios.autopilot.submit_held_order", fake_submit)
    res = tasks_autopilot.release_pending_open_orders()
    assert res == {"released": 2, "candidates": 3, "deferred": 1}
    assert submitted == [o1.id, o3.id]                  # oldest XLE first, MSFT independent
    o2.refresh_from_db()
    assert o2.status == BrokerOrder.STATUS_PENDING_OPEN  # waits for o1 to settle
    # Next tick: o1 filled → o2 goes.
    BrokerOrder.objects.filter(pk=o1.pk).update(status=BrokerOrder.STATUS_FILLED)
    res = tasks_autopilot.release_pending_open_orders()
    assert res["released"] == 1 and res["deferred"] == 0


# --------------------------------------------------------------------------
# Hub: sleeves are mirrors (never double-counted), labelled after the strategy.
# --------------------------------------------------------------------------
def test_portfolios_hub_lists_sleeves_as_mirrors(client, user):
    fund, s = _fund(user, {"A": 100}, enable=False)
    r = client.get("/api/portfolios/hub/")
    assert r.status_code == 200
    body = r.json()
    kinds = {b["kind"] for b in body["books"]}
    assert "sleeve" in kinds and "broker" in kinds
    sleeve = next(b for b in body["books"] if b["kind"] == "sleeve")
    assert sleeve["name"] == "A" and "100.00%" in sleeve["subtitle"]
    assert sleeve["link_route"] == f"/fund/strategies/{s['A'].id}"
    # Headline totals = REAL capital (the auto-created manual book + the broker
    # book); the sleeve is a mirror of the broker book, never summed again.
    assert Decimal(body["totals"]["equity"]) == Decimal("200000")
    assert body["totals"]["mirror_books"] == 1
    assert Decimal(body["totals"]["mirror_equity"]) == Decimal("100000")


# --------------------------------------------------------------------------
# 0037 data migration: roster → sleeves, autopilots disabled (stop-the-world).
# --------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_migration_0037_carries_roster_and_disables_autopilots():
    executor = MigrationExecutor(connection)
    before = [
        ("portfolios", "0036_alter_portfoliostrategy_kind"),
        ("brokers", "0006_p7_autonomous_fund"),
    ]
    executor.migrate(before)
    apps = executor.loader.project_state(before).apps
    OldUser = apps.get_model("accounts", "User")
    OldPortfolio = apps.get_model("portfolios", "Portfolio")
    OldUniverse = apps.get_model("portfolios", "Universe")
    OldStrategy = apps.get_model("portfolios", "PortfolioStrategy")
    OldFund = apps.get_model("portfolios", "AutonomousFund")
    OldAutopilot = apps.get_model("portfolios", "StrategyAutopilot")

    owner = OldUser.objects.create(email="mig@x.test", password="x")
    uni = OldUniverse.objects.create(name="mig-uni")
    fund = OldFund.objects.create(owner=owner, name="Autonomous Fund")
    ids = []
    for i in range(2):
        book = OldPortfolio.objects.create(user=owner, name=f"S{i}", kind="strategy")
        st = OldStrategy.objects.create(user=owner, name=f"S{i}", universe=uni, portfolio=book)
        OldAutopilot.objects.create(strategy=st, is_enabled=True, next_run_at=timezone.now())
        fund.strategies.add(st)
        ids.append(st.id)

    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())

    fund = AutonomousFund.objects.get(name="Autonomous Fund")
    assert fund.broker_account_id is None                # the owner picks it on the Fund tab
    sl = list(fund.sleeves.order_by("strategy_id"))
    assert [x.strategy_id for x in sl] == ids
    assert [str(x.allocation_pct) for x in sl] == ["50.00", "50.00"]
    assert all(x.portfolio.kind == "sleeve" and x.portfolio.cash_balance == 0 for x in sl)
    assert set(fund.strategies.values_list("id", flat=True)) == set(ids)   # through M2M works
    aps = StrategyAutopilot.objects.filter(strategy_id__in=ids)
    assert all(not ap.is_enabled and ap.next_run_at is None for ap in aps)
