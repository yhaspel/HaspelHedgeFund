"""P7 Stage B — deterministic guardrail layer: vol targeting, drawdown
circuit-breaker, liquidity floor, market-hours release, flatten-on-halt,
reconcile-repair tolerance, and the low-vol / TSMOM signals."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.brokers import demo_fills
from apps.brokers.adapters import mock as mock_adapter
from apps.brokers.models import BrokerAccount, BrokerOrder, StrategyBrokerLink
from apps.brokers.reconcile import Drift, drift_within_tolerance
from apps.portfolios import autopilot as bridge
from apps.portfolios import autopilot_risk, cost_model, tasks_autopilot
from apps.portfolios.models import (
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    Position,
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
    return User.objects.create_user(email="p7g@x.test", password="pw-fake-123456789")


def _strategy(user, **kw):
    u = Universe.objects.create(name=f"g-uni-{kw.pop('uni', '0')}")
    for t in ("AAPL", "MSFT"):
        UniverseMembership.objects.create(universe=u, ticker=t, effective_from=dt.date(2020, 1, 1))
    pf = Portfolio.objects.create(user=user, kind=Portfolio.KIND_STRATEGY, name="sb")
    defaults = dict(
        kind=PortfolioStrategy.KIND_LONG_SHORT, max_position_pct=Decimal("0.50"),
        max_sector_pct=Decimal("1.0"), min_trade_notional_usd=Decimal("100"),
        max_turnover_pct=Decimal("1.0"),
    )
    defaults.update(kw)
    return PortfolioStrategy.objects.create(
        user=user, name="S", universe=u, portfolio=pf, **defaults,
    )


def _account(user, *, broker="mock", cash="100000"):
    pf = Portfolio.objects.create(
        user=user, kind=Portfolio.KIND_BROKER, name="bk", cash_balance=Decimal(cash),
    )
    acc = BrokerAccount.objects.create(
        user=user, broker=broker, mode=BrokerAccount.MODE_PAPER,
        account_id=f"{broker}-{user.id}", label="L", portfolio=pf,
        connection_status=BrokerAccount.STATUS_ACTIVE,
    )
    if broker == "mock":
        mock_adapter.seed_demo_book(acc, cash=Decimal(cash))
    return acc


def _autopilot(strategy, account, **kw):
    StrategyBrokerLink.objects.create(strategy=strategy, broker_account=account)
    return StrategyAutopilot.objects.create(
        strategy=strategy, broker_account=account, is_enabled=True, **kw,
    )


# --------------------------------------------------------------------------
# §6.1 Volatility targeting — de-gross only (capped at 1.0).
# --------------------------------------------------------------------------
def test_vol_target_degrosses_high_vol(user, monkeypatch):
    strategy = _strategy(user)
    ap = StrategyAutopilot(strategy=strategy, target_vol_pct=Decimal("10"))
    monkeypatch.setattr(autopilot_risk, "realized_vol", lambda w, a, u: 0.40)  # 40% realised
    scale, audit = autopilot_risk.vol_target_scale({"AAPL": 0.5}, ap, dt.date(2026, 6, 4), user)
    assert scale == pytest.approx(0.25)        # 10% / 40%
    assert audit["scale"] == pytest.approx(0.25)


def test_vol_target_never_levers_up(user, monkeypatch):
    strategy = _strategy(user)
    ap = StrategyAutopilot(strategy=strategy, target_vol_pct=Decimal("10"))
    monkeypatch.setattr(autopilot_risk, "realized_vol", lambda w, a, u: 0.04)  # calm
    scale, _ = autopilot_risk.vol_target_scale({"AAPL": 0.5}, ap, dt.date(2026, 6, 4), user)
    assert scale == 1.0                         # capped — only de-grosses


# --------------------------------------------------------------------------
# §6.3 Drawdown circuit-breaker (off the real-fill equity curve).
# --------------------------------------------------------------------------
def test_drawdown_cold_start_seeds_peak(user, monkeypatch):
    strategy = _strategy(user)
    ap = _autopilot(strategy, _account(user))
    monkeypatch.setattr(autopilot_risk, "broker_equity", lambda a: Decimal("100000"))
    out = autopilot_risk.evaluate_drawdown(ap)
    ap.refresh_from_db()
    assert ap.peak_equity_usd == Decimal("100000")
    assert ap.state == StrategyAutopilot.STATE_ACTIVE
    assert out.get("drawdown_pct") == 0.0


def test_drawdown_soft_then_hard_then_resume(user, monkeypatch):
    strategy = _strategy(user)
    ap = _autopilot(
        strategy, _account(user),
        dd_soft_cut_pct=Decimal("5"), dd_hard_halt_pct=Decimal("7.5"),
    )
    equity = {"v": Decimal("100000")}
    monkeypatch.setattr(autopilot_risk, "broker_equity", lambda a: equity["v"])

    def _eval():
        autopilot_risk.evaluate_drawdown(ap)
        ap.refresh_from_db()
        return ap.state

    _eval()                                          # seed peak 100k
    equity["v"] = Decimal("94000")                   # −6% → soft cut
    assert _eval() == StrategyAutopilot.STATE_SOFT_CUT
    equity["v"] = Decimal("92000")                   # −8% → hard halt
    assert _eval() == StrategyAutopilot.STATE_HALTED

    # Resume (human un-halt) without recovery → re-halts on the next evaluation.
    ap.state = StrategyAutopilot.STATE_ACTIVE
    ap.save(update_fields=["state"])
    assert _eval() == StrategyAutopilot.STATE_HALTED  # fail-safe: peak not reset

    equity["v"] = Decimal("100000")                  # recovered → clears
    assert _eval() == StrategyAutopilot.STATE_ACTIVE


# --------------------------------------------------------------------------
# §6.5 Liquidity floor — caps order size to a fraction of dollar-ADV.
# --------------------------------------------------------------------------
def test_liquidity_floor_caps_order(user, monkeypatch):
    monkeypatch.setattr(demo_fills, "live_price", lambda account, ticker: Decimal("200"))
    # Tiny ADV → 5% of $10k = $500 → 2.5 shares cap (vs the unconstrained ~20).
    monkeypatch.setattr(cost_model, "dollar_adv", lambda t, a, p, **k: Decimal("10000"))
    strategy = _strategy(user)
    ap = _autopilot(strategy, _account(user), liquidity_adv_cap_pct=Decimal("5"))
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=dt.date(2026, 6, 4),
        status=PortfolioTarget.DONE, target_weights={"AAPL": 0.04},
    )
    RebalanceOrder.objects.create(
        target=target, ticker="AAPL", side="buy", quantity=Decimal("1"),
        limit_price=Decimal("200"), reason="open",
        estimated_notional_usd=Decimal("200"), sequence=2,
    )
    bridge.maybe_emit_and_submit(target, link=strategy.broker_links.first(), autopilot=ap)
    ro = RebalanceOrder.objects.get(target=target)
    # Capped from the unconstrained ~20 shares to ~5% of $10k ADV / (buffered)
    # price ≈ 2.49 shares — the exact figure depends on the limit-price buffer.
    assert Decimal("2.4") < ro.quantity < Decimal("2.6")


# --------------------------------------------------------------------------
# §6.6 Market-hours release.
# --------------------------------------------------------------------------
def test_pending_open_released_at_open(user, monkeypatch):
    monkeypatch.setattr(demo_fills, "live_price", lambda account, ticker: Decimal("200"))
    account = _account(user, broker="alpaca_paper")
    strategy = _strategy(user)
    ap = _autopilot(strategy, account)
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=dt.date(2026, 6, 4),
        status=PortfolioTarget.DONE, target_weights={"AAPL": 0.04},
    )
    RebalanceOrder.objects.create(
        target=target, ticker="AAPL", side="buy", quantity=Decimal("1"),
        limit_price=Decimal("200"), reason="open",
        estimated_notional_usd=Decimal("200"), sequence=2,
    )
    monkeypatch.setattr("apps.brokers.market_calendar.is_market_open", lambda *a, **k: False)
    bridge.maybe_emit_and_submit(target, link=strategy.broker_links.first(), autopilot=ap)
    o = BrokerOrder.objects.get(broker_account=account)
    assert o.status == BrokerOrder.STATUS_PENDING_OPEN

    # Re-point the account at the demo broker so release fills deterministically,
    # mark release_after as reached (the next open has arrived), open the market,
    # and run the release task.
    BrokerAccount.objects.filter(pk=account.pk).update(broker="mock")
    BrokerOrder.objects.filter(pk=o.pk).update(
        release_after=timezone.now() - dt.timedelta(minutes=1)
    )
    monkeypatch.setattr("apps.brokers.market_calendar.is_market_open", lambda *a, **k: True)
    res = tasks_autopilot.release_pending_open_orders()
    assert res["released"] == 1
    o.refresh_from_db()
    assert o.status != BrokerOrder.STATUS_PENDING_OPEN


# --------------------------------------------------------------------------
# §6.3 Flatten-on-halt — liquidation routes through the gated path.
# --------------------------------------------------------------------------
def test_flatten_to_cash_emits_closes(user, monkeypatch):
    monkeypatch.setattr(demo_fills, "live_price", lambda account, ticker: Decimal("150"))
    account = _account(user)
    strategy = _strategy(user)
    ap = _autopilot(strategy, account, flatten_on_halt=True)
    Position.objects.create(
        portfolio=account.portfolio, ticker="AAPL",
        quantity=Decimal("10"), avg_cost=Decimal("150"),
    )
    Position.objects.create(
        portfolio=account.portfolio, ticker="MSFT",
        quantity=Decimal("5"), avg_cost=Decimal("150"),
    )

    out = bridge.flatten_to_cash(ap)
    assert out["flattened"] == 2
    orders = BrokerOrder.objects.filter(broker_account=account)
    assert orders.count() == 2
    assert all(o.side == "sell" for o in orders)             # closing longs
    assert all(o.client_order_id.startswith(f"flat-{ap.id}-") for o in orders)


# --------------------------------------------------------------------------
# §11 Reconcile-repair tolerance band.
# --------------------------------------------------------------------------
def test_drift_within_tolerance_band(user):
    pf = Portfolio.objects.create(
        user=user, kind=Portfolio.KIND_BROKER, name="b", cash_balance=Decimal("100000"),
    )
    Position.objects.create(
        portfolio=pf, ticker="AAPL", quantity=Decimal("100"), avg_cost=Decimal("100"),
    )
    # $10 cash + 0.05-share drift on a $10k position → within max(1% MV, $50).
    small = Drift(cash_delta=Decimal("10"), position_deltas={"AAPL": Decimal("0.05")})
    assert drift_within_tolerance(small, pf) is True
    # A 5-share drift ($500) on a $10k position exceeds max(1%·10k=$100, $50).
    big = Drift(cash_delta=Decimal("0"), position_deltas={"AAPL": Decimal("5")})
    assert drift_within_tolerance(big, pf) is False
    # Large cash drift alone trips it.
    cash = Drift(cash_delta=Decimal("5000"), position_deltas={})
    assert drift_within_tolerance(cash, pf) is False


# --------------------------------------------------------------------------
# Signals: low-vol factor + TSMOM.
# --------------------------------------------------------------------------
def test_low_vol_factor_scores():
    from hedgefund_agents.screener.features import ScreenerFeatures, long_score

    calm = ScreenerFeatures(ticker="A", sector="", low_vol=-0.12)   # 12% vol
    wild = ScreenerFeatures(ticker="B", sector="", low_vol=-0.45)   # 45% vol
    w = {"low_vol": 1.0}
    assert long_score(calm, w) > long_score(wild, w)                # calmer scores higher


def test_tsmom_uptrend_downtrend(monkeypatch):
    from hedgefund_agents.macro import tsmom

    class _Bar:
        def __init__(self, c):
            self.close = c

    class _Prov:
        def __init__(self, series):
            self.series = series

        def get_daily_bars(self, ticker, **kw):
            return [_Bar(c) for c in self.series]

    rising = _Prov([100 + i for i in range(260)])
    falling = _Prov([360 - i for i in range(260)])
    assert tsmom.tsmom_score("X", dt.date(2026, 6, 4), rising)["posture"] == "up"
    assert tsmom.tsmom_score("Y", dt.date(2026, 6, 4), falling)["posture"] == "down"
    # Insufficient history → neutral, not an error.
    short = _Prov([100, 101, 102])
    out = tsmom.tsmom_score("Z", dt.date(2026, 6, 4), short)
    assert out["posture"] == "flat" and out["available"] is False
